"""Managing an open position: tightening a stop needs no verdict, widening one is refused."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from atlas_schemas.risk import ManagementAction


@dataclass(frozen=True, slots=True)
class PositionView:
    position_id: UUID
    side: str
    entry_price: Decimal
    stop_price: Decimal
    last_price: Decimal
    opened_at_ms: int


@dataclass(frozen=True, slots=True)
class ManagementDecision:
    approved: bool
    rule_id: str | None
    reason: str


def evaluate(action: ManagementAction, position: PositionView) -> ManagementDecision:
    """Exits are never blocked: CLOSE and TIME_STOP are approved unconditionally (FR-063)."""
    if position.position_id != action.position_id:
        return ManagementDecision(False, "POSITION_MISMATCH", "action targets another position")
    if action.type == "CLOSE":
        return ManagementDecision(True, None, "reduce-only close is never gated")
    if action.type == "TIME_STOP":
        return ManagementDecision(True, None, "time stop is a reduce-only exit")
    if action.type != "MOVE_STOP":
        return ManagementDecision(False, "UNKNOWN_ACTION", f"unhandled action {action.type}")

    new_stop = action.new_stop_price
    if new_stop is None:
        return ManagementDecision(False, "STOP_MISSING", "MOVE_STOP must carry a price")
    if position.side == "BUY":
        if new_stop <= position.stop_price:
            return ManagementDecision(
                False, "STOP_NOT_TIGHTENED", f"{new_stop} does not improve {position.stop_price}"
            )
        if new_stop >= position.last_price:
            return ManagementDecision(
                False, "STOP_ABOVE_MARKET", f"{new_stop} is at or above {position.last_price}"
            )
    elif new_stop >= position.stop_price:
        return ManagementDecision(
            False, "STOP_NOT_TIGHTENED", f"{new_stop} does not improve {position.stop_price}"
        )
    return ManagementDecision(True, None, "stop moved in the only direction that reduces risk")
