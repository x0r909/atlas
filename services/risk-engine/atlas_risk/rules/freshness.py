"""Stage 2: is the picture the proposal was built from still true, and still tradeable?"""

from __future__ import annotations

from atlas_schemas.risk import RuleResult

from atlas_risk import breaks
from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok


def reconciliation_mismatch(ctx: RuleContext) -> RuleResult:
    tripped, observed = breaks.reconciliation_breaker(ctx.snapshot)
    return (
        failed("RECONCILIATION_MISMATCH", observed, "reconciliation_ok=true")
        if tripped
        else ok("RECONCILIATION_MISMATCH")
    )


def stale_price(ctx: RuleContext) -> RuleResult:
    tripped, observed = breaks.stale_price(ctx.snapshot.quote, ctx.config)
    if tripped:
        return failed("STALE_PRICE", observed, f"age_ms<={ctx.config.stale_price_seconds * 1000}")
    unhealthy, health = breaks.data_health_breaker(ctx.snapshot)
    if unhealthy:
        return failed("STALE_PRICE", health, "data_health=OK")
    return ok("STALE_PRICE", observed)


def spread_breaker(ctx: RuleContext) -> RuleResult:
    tripped, observed = breaks.spread_breaker(ctx.snapshot.quote, ctx.config)
    limit = f"spread_ratio<={ctx.config.max_spread_multiple}"
    return failed("SPREAD_BREAKER", observed, limit) if tripped else ok("SPREAD_BREAKER", observed)


def volatility_breaker(ctx: RuleContext) -> RuleResult:
    tripped, observed = breaks.volatility_breaker(ctx.snapshot.volatility_sigma, ctx.config)
    limit = f"move_sigma<={ctx.config.volatility_breaker_sigma}"
    return (
        failed("VOLATILITY_BREAKER", observed, limit)
        if tripped
        else ok("VOLATILITY_BREAKER", observed)
    )


def macro_blackout(ctx: RuleContext) -> RuleResult:
    tripped, observed = breaks.macro_blackout(ctx.snapshot)
    return (
        failed("MACRO_BLACKOUT", observed, "macro_blackout=false")
        if tripped
        else ok("MACRO_BLACKOUT", observed)
    )
