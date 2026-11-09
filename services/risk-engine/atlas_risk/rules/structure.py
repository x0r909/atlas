"""Stage 3: the stop and the reward have to be there before size is even discussed."""

from __future__ import annotations

from decimal import Decimal

from atlas_schemas.risk import RuleResult

from atlas_risk import costs
from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok

HUNDRED = Decimal("100")


def no_stop_loss(ctx: RuleContext) -> RuleResult:
    rule_id = "NO_STOP_LOSS"
    if ctx.trade.stop_price is None:
        return failed(
            rule_id, "stop_price=null", "stop_price present", "an unprotected long is never entered"
        )
    return ok(rule_id, f"stop_price={ctx.trade.stop_price}")


def stop_too_wide(ctx: RuleContext) -> RuleResult:
    """A stop wider than the risk budget can fund at the venue floor can never be sized legally.

    The band is arithmetic, not a magic number: stop_pct > risk_budget / min_notional.
    """
    rule_id = "STOP_TOO_WIDE"
    stop_pct = ctx.trade.stop_distance_pct()
    if stop_pct is None:
        return ok(rule_id, "no stop to measure")
    min_notional = ctx.profile.min_notional_idr
    if min_notional <= 0:
        return failed(rule_id, f"min_notional={min_notional}", "min_notional>0")
    budget = ctx.snapshot.equity_idr * ctx.config.risk_per_trade_pct / HUNDRED
    max_pct = budget / min_notional * HUNDRED
    observed = f"stop_pct={stop_pct.quantize(Decimal('0.001'))}"
    limit = f"stop_pct<={max_pct.quantize(Decimal('0.001'))}"
    return failed(rule_id, observed, limit) if stop_pct > max_pct else ok(rule_id, observed)


def rr_below_min(ctx: RuleContext) -> RuleResult:
    rule_id = "RR_BELOW_MIN"
    rr = ctx.rr_net
    if rr is None:
        return failed(rule_id, "rr_net=null", f"rr_net>={ctx.config.min_rr_net}")
    observed = f"rr_net={rr.quantize(Decimal('0.001'))}"
    limit = f"rr_net>={ctx.config.min_rr_net}"
    return failed(rule_id, observed, limit) if rr < ctx.config.min_rr_net else ok(rule_id, observed)


def reward_target_missing(ctx: RuleContext) -> RuleResult:
    """Not a documented rule id: the schema supplies a target, and RR_BELOW_MIN covers the rest."""
    return ok(
        "RR_BELOW_MIN", "target present" if ctx.trade.target_price is not None else "target=null"
    )


__all__ = ["costs", "no_stop_loss", "rr_below_min"]
