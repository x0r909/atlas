"""T052 / SC-013: Rp100.000 equity cannot fund a 10%-away stop at the venue floor."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from atlas_risk.engine import evaluate


def _small_book(snapshot):
    from dataclasses import replace

    return replace(
        snapshot,
        equity_idr=Decimal("100000"),
        day_start_equity_idr=Decimal("100000"),
        peak_equity_idr=Decimal("100000"),
    )


def test_a_stop_too_wide_for_the_floor_is_rejected_by_the_right_rule(
    snapshot, trade, config, profile
):
    book = _small_book(snapshot)
    wide = replace(trade, proposal_id=uuid4(), stop_price=Decimal("900"))

    evaluation = evaluate(
        trade=wide, snapshot=book, config=config, profile=profile, step_size=Decimal("0.00000001")
    )

    assert evaluation.approved is False
    assert evaluation.primary_rule_id in {"STOP_TOO_WIDE", "MIN_ORDER_EXCEEDS_RISK_BUDGET"}
    result = next(
        r for r in evaluation.rule_results if r.rule_id == "MIN_ORDER_EXCEEDS_RISK_BUDGET"
    )
    assert result.passed is False or evaluation.primary_rule_id == "STOP_TOO_WIDE"


def test_the_floor_itself_decides_and_the_arithmetic_is_reported(snapshot, trade, config, profile):
    """Budget Rp500 at a 10% stop is Rp5.000 of notional: below the Rp10.000 floor."""
    book = _small_book(snapshot)
    wide = replace(trade, proposal_id=uuid4(), stop_price=Decimal("900"))

    evaluation = evaluate(
        trade=wide, snapshot=book, config=config, profile=profile, step_size=Decimal("1")
    )

    stage4 = [
        r for r in evaluation.rule_results if r.rule_id.startswith(("MIN_ORDER", "POSITION_SIZE"))
    ]
    assert any(not r.passed for r in stage4)
    failed = next(r for r in stage4 if not r.passed)
    assert "10000" in (failed.limit or "")
