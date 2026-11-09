"""Stage 6: the throttles that keep a good idea from being repeated sixty times an hour."""

from __future__ import annotations

from datetime import timedelta

from atlas_schemas.risk import RuleResult

from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok


def order_rate_limit(ctx: RuleContext) -> RuleResult:
    rule_id = "ORDER_RATE_LIMIT"
    used = ctx.snapshot.orders_last_hour
    limit = ctx.config.max_orders_per_hour
    return (
        failed(rule_id, f"orders_last_hour={used}", f"<{limit}")
        if used >= limit
        else ok(rule_id, f"orders_last_hour={used}")
    )


def cooldown_active(ctx: RuleContext) -> RuleResult:
    rule_id = "COOLDOWN_ACTIVE"
    snapshot = ctx.snapshot
    losses = snapshot.consecutive_losses
    if losses < ctx.config.cooldown_after_losses or snapshot.last_loss_at is None:
        return ok(rule_id, f"consecutive_losses={losses}")
    until = snapshot.last_loss_at + timedelta(hours=ctx.config.cooldown_hours)
    if snapshot.now < until:
        return failed(rule_id, f"losses={losses} until={until.isoformat()}", "cooldown elapsed")
    return ok(rule_id, f"consecutive_losses={losses}")


def duplicate_proposal(ctx: RuleContext) -> RuleResult:
    rule_id = "DUPLICATE_PROPOSAL"
    proposal_id = ctx.trade.proposal_id
    if proposal_id in ctx.snapshot.recent_proposal_ids:
        return failed(rule_id, f"proposal_id={proposal_id} already seen", "unseen proposal id")
    return ok(rule_id, "first sighting")


def budget_blocked(ctx: RuleContext) -> RuleResult:
    rule_id = "BUDGET_BLOCKED"
    if ctx.snapshot.budget_exhausted:
        return failed(rule_id, "llm budget exhausted for the period", "budget available")
    return ok(rule_id, "budget available")
