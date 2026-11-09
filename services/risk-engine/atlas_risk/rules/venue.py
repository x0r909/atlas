"""Stage 1: is this instrument something we are allowed to trade at all?"""

from __future__ import annotations

from atlas_schemas.risk import RuleResult

from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok

SPOT = "SPOT"


def market_not_spot(ctx: RuleContext) -> RuleResult:
    rule_id = "MARKET_NOT_SPOT"
    market = ctx.snapshot.market_type
    if market != SPOT or ctx.profile.max_leverage != 1:
        return failed(
            rule_id,
            observed=f"market_type={market} max_leverage={ctx.profile.max_leverage}",
            limit="SPOT with max_leverage=1",
            message="derivatives and leverage are out of scope",
        )
    return ok(rule_id, observed=f"market_type={market}")


def venue_not_live_enabled(ctx: RuleContext) -> RuleResult:
    rule_id = "VENUE_NOT_LIVE_ENABLED"
    if ctx.snapshot.require_live_venue and not ctx.snapshot.venue_live_enabled:
        return failed(
            rule_id,
            observed=f"{ctx.profile.venue_id} enabled_for_live=false",
            limit="enabled_for_live=true",
            message="a live order on a venue the operator has not enabled is refused",
        )
    return ok(rule_id, observed=f"live_enabled={ctx.snapshot.venue_live_enabled}")
