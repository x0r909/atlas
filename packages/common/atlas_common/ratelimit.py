"""Token-bucket rate limiting sized from ``venue_profile.rate_limit`` (T033).

Three classes (public/private/order) plus a reserved ``stop`` class, because a protective
stop must never queue behind a research call: the stop budget is 2 create-order calls per
second per pair and 8 venue-wide, independent of whatever the agent loop is doing.

Redis supplies a cross-process ceiling. The local bucket is the fast path; the ceiling is
what stops four uvicorn workers from each spending the whole exchange quota.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "EndpointClass",
    "RateLimitExceeded",
    "RateLimiter",
    "TokenBucket",
]

EndpointClass = Literal["public", "private", "order", "stop"]


class RateLimitExceeded(RuntimeError):
    def __init__(self, bucket: str, retry_after_s: float) -> None:
        super().__init__(f"rate limited on {bucket}; retry in {retry_after_s:.3f}s")
        self.bucket = bucket
        self.retry_after_s = retry_after_s


class TokenBucket:
    """Continuous refill, burst capacity, monotonic clock.

    Refill uses elapsed time rather than a ticker, so an idle process never drifts and a
    paused process does not accumulate more than ``burst``.
    """

    def __init__(
        self,
        rate_per_s: float,
        burst: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self.rate_per_s = rate_per_s
        self.burst = burst
        self._tokens = float(burst)
        self._clock = clock
        self._updated = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate_per_s)
        self._updated = now

    def try_acquire(self, cost: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    def retry_after(self, cost: float = 1.0) -> float:
        self._refill()
        missing = cost - self._tokens
        if missing <= 0:
            return 0.0
        return missing / self.rate_per_s

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens


@dataclass(frozen=True, slots=True)
class ClassSpec:
    rate_per_s: float
    burst: float


@dataclass(frozen=True, slots=True)
class CeilingSpec:
    limit: int
    window_ms: int


DEFAULT_SPECS: dict[EndpointClass, ClassSpec] = {
    "public": ClassSpec(180 / 60, 6),
    "private": ClassSpec(300 / 60, 10),
    "order": ClassSpec(20, 20),
    "stop": ClassSpec(2, 2),
}

# contract: stop class reserves 2/s per pair AND 8/s venue-wide
STOP_VENUE_SPEC = ClassSpec(8, 8)

CEILINGS: dict[EndpointClass, CeilingSpec] = {
    "public": CeilingSpec(180, 60_000),
    "private": CeilingSpec(300, 60_000),
    "order": CeilingSpec(20, 1_000),
    "stop": CeilingSpec(8, 1_000),
}


def specs_from_profile(rate_limit: Mapping[str, Any]) -> dict[EndpointClass, ClassSpec]:
    """Read the numbers from ``venue_profile.rate_limit``; never hard-code an exchange quota."""
    public_rpm = float(rate_limit.get("public_rpm", 180))
    private_rpm = float(rate_limit.get("private_rpm_per_endpoint", 300))
    order_rps = float(rate_limit.get("create_order_rps_per_account_per_pair", 20))
    stop_pair_rps = float(rate_limit.get("stop_budget_rps_per_pair", 2))
    return {
        "public": ClassSpec(public_rpm / 60, max(2.0, public_rpm / 60 * 2)),
        "private": ClassSpec(private_rpm / 60, max(2.0, private_rpm / 60 * 2)),
        "order": ClassSpec(order_rps, order_rps),
        "stop": ClassSpec(stop_pair_rps, stop_pair_rps),
    }


class RateLimiter:
    def __init__(
        self,
        specs: Mapping[EndpointClass, ClassSpec] | None = None,
        *,
        redis: Any | None = None,
        ceilings: Mapping[EndpointClass, CeilingSpec] | None = None,
        clock: Callable[[], float] = time.monotonic,
        namespace: str = "atlas:rl",
    ) -> None:
        self._specs = dict(specs or DEFAULT_SPECS)
        self._redis = redis
        self._ceilings = dict(ceilings or CEILINGS)
        self._clock = clock
        self._namespace = namespace
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._venue_stop: TokenBucket | None = None

    @classmethod
    def from_profile(
        cls,
        rate_limit: Mapping[str, Any],
        *,
        redis: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> RateLimiter:
        return cls(specs_from_profile(rate_limit), redis=redis, clock=clock)

    def _bucket(self, endpoint_class: EndpointClass, key: str) -> TokenBucket:
        bucket_key = (endpoint_class, key)
        bucket = self._buckets.get(bucket_key)
        if bucket is None:
            spec = self._specs[endpoint_class]
            bucket = TokenBucket(spec.rate_per_s, spec.burst, clock=self._clock)
            self._buckets[bucket_key] = bucket
        return bucket

    def _venue_stop_bucket(self) -> TokenBucket:
        if self._venue_stop is None:
            self._venue_stop = TokenBucket(
                STOP_VENUE_SPEC.rate_per_s, STOP_VENUE_SPEC.burst, clock=self._clock
            )
        return self._venue_stop

    async def acquire(
        self,
        endpoint_class: EndpointClass,
        key: str = "default",
        *,
        cost: float = 1.0,
        wait: bool = False,
        max_wait_s: float = 5.0,
    ) -> None:
        """Spend one token, or wait for one. Raises ``RateLimitExceeded`` if wait=False."""
        if endpoint_class == "stop":
            await self._acquire_stop(key, cost=cost, wait=wait, max_wait_s=max_wait_s)
            return
        await self._acquire_one(endpoint_class, key, cost=cost, wait=wait, max_wait_s=max_wait_s)

    async def _acquire_one(
        self,
        endpoint_class: EndpointClass,
        key: str,
        *,
        cost: float,
        wait: bool,
        max_wait_s: float,
    ) -> None:
        bucket = self._bucket(endpoint_class, key)
        while True:
            await self._check_ceiling(endpoint_class, key)
            if bucket.try_acquire(cost):
                return
            delay = bucket.retry_after(cost)
            if not wait:
                raise RateLimitExceeded(f"{endpoint_class}:{key}", delay)
            if delay > max_wait_s:
                raise RateLimitExceeded(f"{endpoint_class}:{key}", delay)
            await asyncio.sleep(delay)

    async def _acquire_stop(
        self,
        pair: str,
        *,
        cost: float,
        wait: bool,
        max_wait_s: float,
    ) -> None:
        pair_bucket = self._bucket("stop", pair)
        venue_bucket = self._venue_stop_bucket()
        while True:
            await self._check_ceiling("stop", pair)
            if pair_bucket.try_acquire(cost) and venue_bucket.try_acquire(cost):
                return
            delay = max(pair_bucket.retry_after(cost), venue_bucket.retry_after(cost))
            if not wait or delay > max_wait_s:
                raise RateLimitExceeded(f"stop:{pair}", delay)
            await asyncio.sleep(delay)

    async def _check_ceiling(self, endpoint_class: EndpointClass, key: str) -> None:
        if self._redis is None:
            return
        spec = self._ceilings.get(endpoint_class)
        if spec is None:
            return
        redis_key = f"{self._namespace}:{endpoint_class}:{key}"
        count = int(await self._redis.incr(redis_key))
        if count == 1:
            await self._redis.pexpire(redis_key, spec.window_ms)
        if count > spec.limit:
            ttl_ms = int(await self._redis.pttl(redis_key))
            raise RateLimitExceeded(f"{endpoint_class}:{key}", max(ttl_ms, 1) / 1000)
