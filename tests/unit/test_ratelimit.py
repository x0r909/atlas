"""Rate limiter: the stop class must not be starved by agent traffic."""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_common.ratelimit import (
    ClassSpec,
    RateLimiter,
    RateLimitExceeded,
    TokenBucket,
    specs_from_profile,
)

PROFILE_RATE_LIMIT = {
    "public_rpm": 180,
    "private_rpm_per_endpoint": 300,
    "create_order_rps_per_account_per_pair": 20,
    "stop_budget_rps_per_pair": 2,
    "stop_budget_rps_venue": 8,
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_bucket_refills_continuously_and_caps_at_burst():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=2, burst=3, clock=clock)
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False
    clock.advance(0.25)
    assert bucket.try_acquire() is False
    clock.advance(0.25)
    assert bucket.try_acquire() is True
    clock.advance(100)
    assert bucket.tokens == pytest.approx(3.0)


def test_bucket_never_refills_beyond_burst_after_a_long_pause():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=10, burst=5, clock=clock)
    clock.advance(3600)
    assert bucket.tokens == pytest.approx(5.0)


def test_profile_numbers_are_used_not_hard_coded_exchange_quotas():
    specs = specs_from_profile(PROFILE_RATE_LIMIT)
    assert specs["public"].rate_per_s == pytest.approx(3.0)
    assert specs["private"].rate_per_s == pytest.approx(5.0)
    assert specs["order"].rate_per_s == pytest.approx(20.0)
    assert specs["stop"].rate_per_s == pytest.approx(2.0)


async def test_public_calls_are_capped_per_second_without_waiting():
    clock = FakeClock()
    limiter = RateLimiter({"public": ClassSpec(2, 2), "stop": ClassSpec(2, 2)}, clock=clock)
    await limiter.acquire("public", "indodax")
    await limiter.acquire("public", "indodax")
    with pytest.raises(RateLimitExceeded) as excinfo:
        await limiter.acquire("public", "indodax")
    assert excinfo.value.retry_after_s == pytest.approx(0.5)
    clock.advance(0.5)
    await limiter.acquire("public", "indodax")


async def test_order_budget_is_per_pair_so_one_pair_cannot_exhaust_another():
    clock = FakeClock()
    limiter = RateLimiter({"order": ClassSpec(1, 1)}, clock=clock)
    await limiter.acquire("order", "btcidr")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire("order", "btcidr")
    await limiter.acquire("order", "ethidr")


async def test_stop_class_has_its_own_two_per_second_per_pair_budget():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    await limiter.acquire("stop", "btcidr")
    await limiter.acquire("stop", "btcidr")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire("stop", "btcidr")


async def test_stop_class_also_respects_the_venue_wide_eight_per_second_budget():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for pair in ("btcidr", "ethidr", "usdtidr", "solcidr"):
        await limiter.acquire("stop", pair)
        await limiter.acquire("stop", pair)
    with pytest.raises(RateLimitExceeded) as excinfo:
        await limiter.acquire("stop", "bnbidr")
    assert excinfo.value.bucket == "stop:bnbidr"


async def test_stop_traffic_is_unaffected_by_a_saturated_public_bucket():
    clock = FakeClock()
    limiter = RateLimiter({"public": ClassSpec(1, 1), "stop": ClassSpec(2, 2)}, clock=clock)
    await limiter.acquire("public", "indodax")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire("public", "indodax")
    # The stop budget is separate: protective orders still go.
    await limiter.acquire("stop", "btcidr")


async def test_waiting_mode_sleeps_until_the_token_exists(monkeypatch):
    clock = FakeClock()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr("atlas_common.ratelimit.asyncio.sleep", fake_sleep)
    limiter = RateLimiter({"public": ClassSpec(1, 1)}, clock=clock)
    await limiter.acquire("public", "x")
    await limiter.acquire("public", "x", wait=True)
    assert slept and slept[0] == pytest.approx(1.0)


async def test_waiting_longer_than_the_cap_raises_instead_of_hanging(monkeypatch):
    limiter = RateLimiter({"public": ClassSpec(0.1, 1)})
    await limiter.acquire("public", "x")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire("public", "x", wait=True, max_wait_s=1.0)


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def pexpire(self, key: str, ms: int) -> bool:
        self.ttls.setdefault(key, ms)
        return True

    async def pttl(self, key: str) -> int:
        return self.ttls.get(key, 1)


async def test_redis_ceiling_catches_what_one_local_bucket_would_allow():
    redis = FakeRedis()
    limiter = RateLimiter({"public": ClassSpec(100, 100)}, redis=redis)
    await limiter.acquire("public", "indodax")
    with pytest.raises(RateLimitExceeded) as excinfo:
        for _ in range(200):
            await limiter.acquire("public", "indodax")
    assert excinfo.value.retry_after_s >= 0.001


def test_decimal_profile_values_are_tolerated():
    specs = specs_from_profile({"public_rpm": Decimal("60"), "stop_budget_rps_per_pair": 4})
    assert specs["public"].rate_per_s == pytest.approx(1.0)
    assert specs["stop"].rate_per_s == pytest.approx(4.0)


def test_invalid_rates_fail_loudly():
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)
