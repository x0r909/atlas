"""T065: the only door an order can walk through.

The gate answers one question — may this signed order be placed right now — and then spends
the verdict exactly once in the same transaction that records the order. `decide` is pure so
the rejection matrix is a table rather than an integration test; `consume` is the part that
needs a database and is the only part that can lose a race.

Order of checks follows contracts/decision-risk.md §5 so a refusal names the real reason: an
expired verdict is reported as expired even if the system is also halted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from atlas_schemas.order import REFUSALS, SignedOrder, VerdictRefusal
from atlas_schemas.risk import RiskVerdict
from atlas_schemas.verdict_sign import verify_order_with_verdict

# Modes in which an order may open exposure at all.
TRADABLE_MODES = frozenset({"CO_PILOT", "AUTONOMOUS"})


class Refused(Exception):
    """A gate refusal. `code` is one of the contract's VERDICT_* strings."""

    def __init__(self, code: VerdictRefusal, detail: str = "") -> None:
        if code not in REFUSALS:
            raise ValueError(f"{code} is not a contract refusal code")
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class _Connection(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def execute(self, query: str, *args: Any) -> str: ...


@dataclass(frozen=True, slots=True)
class GateInputs:
    """Everything the decision needs, gathered by the caller in one read."""

    verdict: RiskVerdict | None
    order: SignedOrder
    mode: str
    venue_live_enabled: bool
    now: datetime
    consumed_at: datetime | None = None


def order_fields(order: SignedOrder) -> dict[str, Any]:
    """The fields the order signature covers (contract §5)."""
    return {
        "signed_order_id": str(order.signed_order_id),
        "verdict_id": str(order.verdict_id),
        "proposal_id": str(order.proposal_id),
        "venue_id": order.venue_id,
        "pair": order.pair,
        "side": order.side,
        "type": order.type,
        "quantity": order.quantity,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "client_order_id": order.client_order_id,
        "signed_at": order.signed_at,
        "expires_at": order.expires_at,
    }


def decide(inputs: GateInputs, *, key: str | bytes) -> None:
    """Raise `Refused` unless this order may be placed exactly now."""
    verdict, order = inputs.verdict, inputs.order
    if not verify_order_with_verdict(
        order_fields(order), str(order.verdict_id), order.signature, key
    ):
        raise Refused("VERDICT_SIGNATURE_INVALID", "order signature does not cover its payload")
    if order.expires_at <= inputs.now:
        raise Refused("VERDICT_EXPIRED", f"order lifetime ended at {order.expires_at.isoformat()}")
    if verdict is None:
        raise Refused("VERDICT_UNKNOWN_ID", f"no verdict {order.verdict_id}")
    if not verdict.approved:
        raise Refused("VERDICT_NOT_APPROVED", f"verdict {verdict.verdict_id} was rejected")
    if inputs.consumed_at is not None:
        raise Refused("VERDICT_CONSUMED", f"verdict {verdict.verdict_id} was already spent")
    if verdict.expires_at <= inputs.now:
        raise Refused("VERDICT_EXPIRED", f"verdict expired at {verdict.expires_at.isoformat()}")
    if verdict.sized_quantity is None or verdict.sized_quantity != order.quantity:
        raise Refused(
            "VERDICT_PAYLOAD_MISMATCH",
            f"order asks for {order.quantity}, verdict approved {verdict.sized_quantity}",
        )
    if order.proposal_id != verdict.proposal_id:
        raise Refused("VERDICT_PAYLOAD_MISMATCH", "order cites another proposal than the verdict")
    # Mode last: a HALTED system should say so rather than blame the verdict.
    mode_gate(order, inputs.mode, inputs.venue_live_enabled)


def mode_gate(order: SignedOrder, mode: str, venue_live_enabled: bool) -> None:
    """FR-001/FR-002/FR-063: halting stops entries, never exits."""
    if mode == "HALTED" and not order.is_reduce_only:
        raise Refused("MODE_NOT_TRADABLE", "the system is HALTED; entries are refused")
    if venue_live_enabled and not order.is_reduce_only and mode not in TRADABLE_MODES:
        raise Refused("MODE_NOT_TRADABLE", f"{mode} may not open a position on a live venue")


_SELECT_VERDICT = """
SELECT consumed_at
  FROM risk_verdict
 WHERE id = $1
   FOR UPDATE
"""

_CURRENT_MODE = """
SELECT mode::text AS mode
  FROM mode_state
 ORDER BY seq DESC
 LIMIT 1
"""

_VENUE_LIVE = """
SELECT enabled_for_live
  FROM venue
 WHERE id = $1
"""

_CONSUME = """
UPDATE risk_verdict
   SET consumed_at = $3,
       consumed_by_order_id = $2
 WHERE id = $1
   AND consumed_at IS NULL
RETURNING id
"""

_RECORD_ORDER_ROW = """
INSERT INTO "order" (
    id, venue_id, client_order_id, signed_order_id, pair, side, type,
    quantity, limit_price, status
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'PENDING_NEW')
"""

_RECORD_ORDER = """
INSERT INTO signed_order (
    id, verdict_id, proposal_id, venue_id, pair, side, type, quantity,
    limit_price, stop_price, client_order_id, signature, signed_at, expires_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""


async def consume(session: _Connection, order: SignedOrder) -> UUID:
    """Spend the verdict and record the order, atomically. Returns the `order` row id.

    Two callers racing on the same verdict both pass `decide`; exactly one wins the UPDATE,
    because the first takes the row lock and the second re-reads `consumed_at` as non-null.

    The order is inserted first because `risk_verdict_consumption_paired` requires the spend
    and the order id to land together; a losing racer therefore leaves an order row behind
    until its transaction rolls back, which is why this must run inside one. The UNIQUE
    constraint on `signed_order.verdict_id` refuses the second insert even if the UPDATE were
    somehow reached twice.
    """
    row = await session.fetchrow(_SELECT_VERDICT, order.verdict_id)
    if row is None:
        raise Refused("VERDICT_UNKNOWN_ID", f"no verdict {order.verdict_id}")
    mode_row = await session.fetchrow(_CURRENT_MODE)
    venue_row = await session.fetchrow(_VENUE_LIVE, order.venue_id)
    if mode_row is None or venue_row is None:
        raise Refused("MODE_NOT_TRADABLE", "the mode or the venue is not recorded")
    mode_gate(order, mode_row["mode"], bool(venue_row["enabled_for_live"]))
    if row["consumed_at"] is not None:
        # The lock makes a racing caller wait here and then see the spend, so refusal is a
        # decision rather than an exception from a constraint.
        raise Refused("VERDICT_CONSUMED", f"verdict {order.verdict_id} was already spent")
    # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
    await session.execute(
        _RECORD_ORDER,
        order.signed_order_id,
        order.verdict_id,
        order.proposal_id,
        order.venue_id,
        order.pair,
        order.side,
        order.type,
        order.quantity,
        order.limit_price,
        order.stop_price,
        order.client_order_id,
        order.signature,
        order.signed_at,
        order.expires_at,
    )
    order_row_id = uuid4()
    await session.execute(
        _RECORD_ORDER_ROW,
        order_row_id,
        order.venue_id,
        order.client_order_id,
        order.signed_order_id,
        order.pair,
        order.side,
        order.type,
        order.quantity,
        order.limit_price,
    )
    spent = await session.fetchrow(_CONSUME, order.verdict_id, order_row_id, order.signed_at)
    if spent is None:
        raise Refused("VERDICT_CONSUMED", f"verdict {order.verdict_id} was already spent")
    return order_row_id
