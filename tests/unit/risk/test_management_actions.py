"""T061: exits are never gated, and a stop may only move in the safe direction."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from atlas_risk.management import PositionView, evaluate
from atlas_schemas.risk import ManagementAction

POSITION = PositionView(
    position_id=uuid4(),
    side="BUY",
    entry_price=Decimal("1000"),
    stop_price=Decimal("950"),
    last_price=Decimal("1100"),
    opened_at_ms=0,
)


def _action(kind, price=None, position=POSITION):
    return ManagementAction(
        action_id=uuid4(),
        position_id=position.position_id,
        type=kind,
        new_stop_price=price,
        reason="test",
    )


def test_tightening_the_stop_is_approved() -> None:
    decision = evaluate(_action("MOVE_STOP", Decimal("1000")), POSITION)
    assert decision.approved is True
    assert decision.rule_id is None


def test_widening_the_stop_is_refused() -> None:
    decision = evaluate(_action("MOVE_STOP", Decimal("900")), POSITION)
    assert decision.approved is False
    assert decision.rule_id == "STOP_NOT_TIGHTENED"


def test_a_stop_at_or_above_the_market_is_refused() -> None:
    decision = evaluate(_action("MOVE_STOP", Decimal("1100")), POSITION)
    assert decision.approved is False
    assert decision.rule_id == "STOP_ABOVE_MARKET"


def test_close_needs_no_verdict_and_is_always_approved() -> None:
    decision = evaluate(_action("CLOSE"), POSITION)
    assert decision.approved is True


def test_time_stop_is_always_approved() -> None:
    decision = evaluate(_action("TIME_STOP"), POSITION)
    assert decision.approved is True


def test_an_action_for_another_position_is_refused() -> None:
    other = (
        PositionView(**{**POSITION.__dict__, "position_id": uuid4()})
        if hasattr(POSITION, "__dict__")
        else None
    )
    from dataclasses import replace

    other = replace(POSITION, position_id=uuid4())
    decision = evaluate(_action("CLOSE"), other)
    assert decision.approved is False
    assert decision.rule_id == "POSITION_MISMATCH"
