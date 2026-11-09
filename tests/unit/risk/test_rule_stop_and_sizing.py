"""T054: the stop exists, sits inside the expressible band, and size follows from it."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from atlas_risk import costs
from atlas_risk.engine import evaluate


def _rule(evaluation, rule_id):
    return next(r for r in evaluation.rule_results if r.rule_id == rule_id)


def test_a_missing_stop_is_rejected(snapshot, trade, config, profile):
    naked = replace(trade, stop_price=None)
    evaluation = evaluate(
        trade=naked, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.primary_rule_id == "NO_STOP_LOSS"
    assert _rule(evaluation, "NO_STOP_LOSS").passed is False


def test_a_stop_wider_than_the_budget_can_fund_is_rejected(snapshot, trade, config, profile):
    # equity 1,000,000 -> budget 5,000; floor 10,000 -> nothing wider than 50% is expressible.
    absurd = replace(trade, stop_price=Decimal("400"))
    evaluation = evaluate(
        trade=absurd, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.primary_rule_id == "STOP_TOO_WIDE"


def test_sizing_is_the_budget_divided_by_the_stop_distance(snapshot, trade, config, profile):
    evaluation = evaluate(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.sizing is not None
    assert evaluation.sizing.quantity == Decimal("100")
    assert evaluation.sizing.realised_risk_idr > Decimal("5000")  # budget plus round trip


def test_rounding_is_always_down_and_never_up(trade, profile, config):
    sized = costs.size(
        proposal=trade,  # type: ignore[arg-type]
        equity_idr=Decimal("1000000"),
        risk_per_trade_pct=Decimal("0.500"),
        max_position_pct=Decimal("25"),
        min_notional_idr=Decimal("10000"),
        step_size=Decimal("0.3"),
        profile=profile,
        max_slippage_pct=config.max_slippage_pct,
    )
    assert sized is not None
    assert sized.quantity == Decimal("99.9")  # 100 would exceed the 5,000 IDR budget
    assert sized.quantity * trade.entry_price <= Decimal("100000")


def test_a_step_larger_than_the_budget_returns_no_size(trade, profile, config):
    assert (
        costs.size(
            proposal=trade,  # type: ignore[arg-type]
            equity_idr=Decimal("1000000"),
            risk_per_trade_pct=Decimal("0.500"),
            max_position_pct=Decimal("25"),
            min_notional_idr=Decimal("10000"),
            step_size=Decimal("1000"),
            profile=profile,
            max_slippage_pct=config.max_slippage_pct,
        )
        is None
    )
