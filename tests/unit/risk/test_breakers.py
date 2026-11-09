"""T059a: the breakers are pure functions and read their limits from configuration."""

from __future__ import annotations

from decimal import Decimal

from atlas_risk import breaks
from atlas_risk.models import HEALTH_DOWN, HEALTH_OK, HEALTH_STALE, Quote, RiskConfig


def test_stale_price_uses_the_configured_seconds() -> None:
    quote = Quote(
        bid=Decimal("1"),
        ask=Decimal("2"),
        last=Decimal("1.5"),
        age_ms=11_000,
        median_spread_24h=Decimal("1"),
    )
    assert breaks.stale_price(quote, RiskConfig(version=1))[0] is True
    assert breaks.stale_price(quote, RiskConfig(version=1, stale_price_seconds=30))[0] is False


def test_spread_breaker_compares_to_the_median_not_to_a_fixed_number() -> None:
    # A one-IDR spread against a 0.5 median is a ratio of 2, so the limit decides the outcome.
    quote = Quote(
        bid=Decimal("99"),
        ask=Decimal("100"),
        last=Decimal("100"),
        age_ms=10,
        median_spread_24h=Decimal("0.5"),
    )
    strict = RiskConfig(version=1, max_spread_multiple=Decimal("1.5"))
    tripped, observed = breaks.spread_breaker(quote, strict)
    assert tripped is True
    assert "spread_ratio=2" in observed
    relaxed = RiskConfig(version=1, max_spread_multiple=Decimal("3"))
    assert breaks.spread_breaker(quote, relaxed)[0] is False


def test_volatility_breaker_is_configurable() -> None:
    config = RiskConfig(version=1, volatility_breaker_sigma=Decimal("5"))
    assert breaks.volatility_breaker(Decimal("5.1"), config)[0] is True
    assert breaks.volatility_breaker(Decimal("5"), config)[0] is False


def test_data_health_breaker_treats_stale_and_down_as_broken() -> None:
    assert breaks.data_health_breaker(_snapshot(HEALTH_OK))[0] is False
    assert breaks.data_health_breaker(_snapshot(HEALTH_STALE))[0] is True
    assert breaks.data_health_breaker(_snapshot(HEALTH_DOWN))[0] is True


def _snapshot(health):
    from datetime import UTC, datetime

    from atlas_risk.models import RiskSnapshot

    return RiskSnapshot(
        now=datetime(2024, 5, 1, tzinfo=UTC),
        venue_live_enabled=True,
        market_type="SPOT",
        quote=Quote(
            bid=Decimal("1"),
            ask=Decimal("2"),
            last=Decimal("1.5"),
            age_ms=1,
            median_spread_24h=Decimal("1"),
        ),
        equity_idr=Decimal("1000000"),
        day_start_equity_idr=Decimal("1000000"),
        peak_equity_idr=Decimal("1000000"),
        data_health=health,
    )
