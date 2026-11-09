"""Market-condition breakers. Each is a pure predicate with its limit taken from config."""

from __future__ import annotations

from decimal import Decimal

from atlas_risk.models import HEALTH_DOWN, HEALTH_STALE, Quote, RiskConfig, RiskSnapshot


def stale_price(quote: Quote, config: RiskConfig) -> tuple[bool, str]:
    limit_ms = config.stale_price_seconds * 1000
    return quote.age_ms > limit_ms, f"age_ms={quote.age_ms} limit_ms={limit_ms}"


def spread_breaker(quote: Quote, config: RiskConfig) -> tuple[bool, str]:
    ratio = quote.spread_ratio
    if ratio is None:
        # No median means no reference: refuse rather than assume a healthy spread.
        return True, "median_spread_24h=0 (no reference to compare against)"
    return ratio > config.max_spread_multiple, (
        f"spread_ratio={ratio.quantize(Decimal('0.001'))} limit={config.max_spread_multiple}"
    )


def volatility_breaker(sigma: Decimal, config: RiskConfig) -> tuple[bool, str]:
    return sigma > config.volatility_breaker_sigma, (
        f"move_sigma={sigma} limit={config.volatility_breaker_sigma}"
    )


def data_health_breaker(snapshot: RiskSnapshot) -> tuple[bool, str]:
    broken = snapshot.data_health in {HEALTH_STALE, HEALTH_DOWN}
    return broken, f"data_health={snapshot.data_health}"


def reconciliation_breaker(snapshot: RiskSnapshot) -> tuple[bool, str]:
    return not snapshot.reconciliation_ok, f"reconciliation_ok={snapshot.reconciliation_ok}"


def macro_blackout(snapshot: RiskSnapshot) -> tuple[bool, str]:
    return snapshot.macro_blackout, f"macro_blackout={snapshot.macro_blackout}"
