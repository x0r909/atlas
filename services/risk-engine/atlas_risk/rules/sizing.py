"""Stage 4: can the risk budget actually fund a legal order at this venue?"""

from __future__ import annotations

from atlas_schemas.risk import RuleResult

from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok


def min_order_exceeds_risk_budget(ctx: RuleContext) -> RuleResult:
    rule_id = "MIN_ORDER_EXCEEDS_RISK_BUDGET"
    min_notional = ctx.profile.min_notional_idr
    if ctx.sizing is None:
        return failed(
            rule_id,
            observed=f"sized_notional=0 min_notional={min_notional}",
            limit=f"notional>={min_notional}",
            message="the risk budget cannot fund one minimum order at this stop distance",
        )
    if ctx.sizing.notional_idr < min_notional:
        return failed(
            rule_id,
            observed=f"notional={ctx.sizing.notional_idr}",
            limit=f"notional>={min_notional}",
        )
    return ok(rule_id, f"notional={ctx.sizing.notional_idr}")


def position_size_exceeds_max(ctx: RuleContext) -> RuleResult:
    rule_id = "POSITION_SIZE_EXCEEDS_MAX"
    if ctx.sizing is None:
        return ok(rule_id, "no size to cap")
    cap = ctx.snapshot.equity_idr * ctx.config.max_position_pct / 100
    observed = f"notional={ctx.sizing.notional_idr}"
    limit = f"<={cap.quantize(cap)}"
    return (
        failed(rule_id, observed, limit) if ctx.sizing.notional_idr > cap else ok(rule_id, observed)
    )
