"""Stage 5: what the book already carries, and how much of the day's loss budget is gone."""

from __future__ import annotations

from decimal import Decimal

from atlas_schemas.risk import RuleResult

from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok

HUNDRED = Decimal("100")
_TWO_PLACES = Decimal("0.01")


def _pct(value: Decimal) -> str:
    return str(value.quantize(_TWO_PLACES))


def asset_exposure_exceeds_max(ctx: RuleContext) -> RuleResult:
    rule_id = "ASSET_EXPOSURE_EXCEEDS_MAX"
    added = ctx.sizing.notional_idr if ctx.sizing is not None else Decimal("0")
    after = ctx.snapshot.asset_exposure_idr + added
    used = after / ctx.snapshot.equity_idr * HUNDRED
    limit = ctx.config.max_asset_exposure_pct
    return (
        failed(rule_id, f"asset_exposure_pct={_pct(used)}", f"<={limit}")
        if used > limit
        else ok(rule_id, f"asset_exposure_pct={_pct(used)}")
    )


def total_exposure_exceeds_max(ctx: RuleContext) -> RuleResult:
    rule_id = "TOTAL_EXPOSURE_EXCEEDS_MAX"
    added = ctx.sizing.notional_idr if ctx.sizing is not None else Decimal("0")
    after = ctx.snapshot.total_exposure_idr + added
    used = after / ctx.snapshot.equity_idr * HUNDRED
    limit = ctx.config.max_total_exposure_pct
    return (
        failed(rule_id, f"total_exposure_pct={_pct(used)}", f"<={limit}")
        if used > limit
        else ok(rule_id, f"total_exposure_pct={_pct(used)}")
    )


def daily_loss_limit(ctx: RuleContext) -> RuleResult:
    rule_id = "DAILY_LOSS_LIMIT"
    loss = ctx.snapshot.daily_loss_pct
    limit = ctx.config.max_daily_loss_pct
    return (
        failed(rule_id, f"daily_loss_pct={_pct(loss)}", f"<={limit}")
        if loss >= limit
        else ok(rule_id, f"daily_loss_pct={_pct(loss)}")
    )


def max_drawdown(ctx: RuleContext) -> RuleResult:
    rule_id = "MAX_DRAWDOWN"
    drawdown = ctx.snapshot.drawdown_pct
    limit = ctx.config.max_drawdown_pct
    return (
        failed(rule_id, f"drawdown_pct={_pct(drawdown)}", f"<={limit}")
        if drawdown >= limit
        else ok(rule_id, f"drawdown_pct={_pct(drawdown)}")
    )


def max_open_positions(ctx: RuleContext) -> RuleResult:
    rule_id = "MAX_OPEN_POSITIONS"
    open_now = ctx.snapshot.open_positions
    limit = ctx.config.max_open_positions
    return (
        failed(rule_id, f"open_positions={open_now}", f"<{limit}")
        if open_now >= limit
        else ok(rule_id, f"open_positions={open_now}")
    )
