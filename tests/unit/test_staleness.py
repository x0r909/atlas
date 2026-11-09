"""Staleness is a number, not a feeling: the breaker thresholds are pinned here.

Each threshold is pinned at its boundary: the exact value passes, one unit past fails.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_features.staleness import (
    MAX_QUOTE_AGE_MS,
    MOVE_SIGMA_MULTIPLE,
    SPREAD_RATIO_MULTIPLE,
    StalenessMonitor,
)

D = Decimal


def _monitor() -> StalenessMonitor:
    return StalenessMonitor(half_life_bars=10)


def test_the_documented_thresholds_are_the_ones_the_code_uses() -> None:
    assert MAX_QUOTE_AGE_MS == 10_000
    assert D("3") == SPREAD_RATIO_MULTIPLE
    assert D("5") == MOVE_SIGMA_MULTIPLE


def test_a_quote_older_than_ten_seconds_breaches() -> None:
    monitor = _monitor()
    verdict = monitor.evaluate(
        now_ms=1_000_000,
        quote_ts_ms=1_000_000 - 10_001,
        spread=D("1"),
        median_spread=D("1"),
        last_price=D("100"),
        reference_price=D("100"),
    )
    assert verdict.breaches == ("STALE_QUOTE",)
    assert verdict.quote_age_ms == 10_001


def test_a_quote_exactly_ten_seconds_old_does_not_breach() -> None:
    monitor = _monitor()
    verdict = monitor.evaluate(
        now_ms=1_000_000,
        quote_ts_ms=1_000_000 - 10_000,
        spread=D("1"),
        median_spread=D("1"),
        last_price=D("100"),
        reference_price=D("100"),
    )
    assert verdict.breaches == ()


def test_a_spread_above_three_times_the_median_breaches_and_exactly_three_does_not() -> None:
    monitor = _monitor()
    wide = monitor.evaluate(
        now_ms=0,
        quote_ts_ms=0,
        spread=D("3.01"),
        median_spread=D("1"),
        last_price=D("100"),
        reference_price=D("100"),
    )
    assert wide.breaches == ("SPREAD_BREAKER",)
    assert wide.spread_ratio == D("3.01")

    at_limit = monitor.evaluate(
        now_ms=0,
        quote_ts_ms=0,
        spread=D("3"),
        median_spread=D("1"),
        last_price=D("100"),
        reference_price=D("100"),
    )
    assert at_limit.breaches == ()


def test_sigma_comes_from_one_minute_returns_and_five_sigma_breaches() -> None:
    monitor = _monitor()
    # A steady 1 % up/down alternation: returns are ±0.0101…, so sigma is ~1 %.
    price = D("100")
    for index in range(40):
        price = price * (D("1.01") if index % 2 == 0 else D("0.9900990099009900990099009901"))
        monitor.observe_return(
            price=D("100") * (D("1.01") if index % 2 == 0 else D("1")),
        )
    sigma = monitor.sigma()
    assert sigma is not None
    assert D("0.008") < sigma < D("0.012")

    breach = monitor.evaluate(
        now_ms=0,
        quote_ts_ms=0,
        spread=D("1"),
        median_spread=D("1"),
        last_price=D("100") * (1 + sigma * D("5.5")),
        reference_price=D("100"),
    )
    assert "MOVE_BREAKER" in breach.breaches
    assert breach.move_sigma is not None
    assert breach.move_sigma > MOVE_SIGMA_MULTIPLE


def test_a_move_inside_four_sigma_does_not_breach() -> None:
    monitor = _monitor()
    for _ in range(40):
        monitor.observe_return(price=D("100"))
    monitor.observe_return(price=D("101"))
    sigma = monitor.sigma()
    assert sigma is not None and sigma > 0
    calm = monitor.evaluate(
        now_ms=0,
        quote_ts_ms=0,
        spread=D("1"),
        median_spread=D("1"),
        last_price=D("100") * (1 + sigma * D("4")),
        reference_price=D("100"),
    )
    assert "MOVE_BREAKER" not in calm.breaches


def test_without_enough_history_there_is_no_sigma_and_no_move_breaker() -> None:
    monitor = _monitor()
    monitor.observe_return(price=D("100"))
    assert monitor.sigma() is None
    verdict = monitor.evaluate(
        now_ms=0,
        quote_ts_ms=0,
        spread=D("1"),
        median_spread=D("1"),
        last_price=D("500"),
        reference_price=D("100"),
    )
    assert verdict.breaches == ()
    assert verdict.move_sigma is None


def test_all_breaches_are_reported_not_just_the_first() -> None:
    monitor = _monitor()
    verdict = monitor.evaluate(
        now_ms=1_000_000,
        quote_ts_ms=0,
        spread=D("9"),
        median_spread=D("1"),
        last_price=D("100"),
        reference_price=D("100"),
    )
    assert verdict.breaches == ("STALE_QUOTE", "SPREAD_BREAKER")


def test_a_zero_median_spread_is_refused_rather_than_dividing_by_zero() -> None:
    monitor = _monitor()
    with pytest.raises(ValueError, match="median spread"):
        monitor.evaluate(
            now_ms=0,
            quote_ts_ms=0,
            spread=D("1"),
            median_spread=D("0"),
            last_price=D("100"),
            reference_price=D("100"),
        )
