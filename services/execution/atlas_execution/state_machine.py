"""T069: the order state machine — one graph, one audit record per move.

`data-model.md` §5 is the authority. The graph is data (`LEGAL_TRANSITIONS`) so a refusal can
name both ends instead of describing an if-tree. Two states need special care:

* `UNKNOWN` is non-terminal: it is a state a query resolves, never a resting place. Only one
  order may be in it per venue at a time (FR-069, data-model invariant 9); the guard lives
  here because this is the only code that writes `status`.
* `FILLED`, `CANCELED`, `REJECTED` and `EXPIRED` are terminal. A transition out of one is
  refused, recorded, and raised — the record is what lets an operator see the attempt.

`FILLED -> CANCELED` in particular is refused rather than treated as a correction: a fill is
an executed trade, and cancelling the row would make the position disagree with the ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from atlas_schemas.order import OrderStatus

AUDIT_ORDER = "ORDER"


class OrderState(StrEnum):
    """Mirror of the `order_status` enum in migration 0003."""

    PENDING_NEW = "PENDING_NEW"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


LEGAL_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING_NEW: frozenset({OrderState.OPEN, OrderState.REJECTED, OrderState.UNKNOWN}),
    OrderState.OPEN: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.CANCELED, OrderState.UNKNOWN}
    ),
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.OPEN,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}

TERMINAL_STATES = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }
)

_READ_ORDER = """
SELECT id, venue_id, status
  FROM "order"
 WHERE id = $1
"""

_UNKNOWN_EXISTS = """
SELECT id
  FROM "order"
 WHERE venue_id = $1
   AND status = 'UNKNOWN'
   AND id <> $2
 LIMIT 1
"""

_UPDATE_STATUS = """
UPDATE "order"
   SET status = $2::order_status,
       filled_quantity = COALESCE($3, filled_quantity),
       unknown_since = CASE
           WHEN $2::order_status = 'UNKNOWN' THEN now()
           ELSE NULL
       END,
       resolved_at = CASE
           WHEN $2::order_status <> 'UNKNOWN' AND status = 'UNKNOWN' THEN now()
           ELSE resolved_at
       END,
       resolution_attempts = CASE
           WHEN $2::order_status = 'UNKNOWN' THEN 0
           ELSE resolution_attempts
       END,
       updated_at = now()
 WHERE id = $1
"""


class IllegalTransition(Exception):
    """The graph does not allow this move. The attempt is audited before this is raised."""

    code = "ILLEGAL_TRANSITION"

    def __init__(self, order_id: UUID, from_state: OrderState, to_state: OrderState) -> None:
        super().__init__(f"{order_id}: {from_state} -> {to_state} is not a legal transition")
        self.order_id = order_id
        self.from_state = from_state
        self.to_state = to_state


class VenueFrozen(Exception):
    """Another order on this venue is already `UNKNOWN` (FR-069, invariant 9)."""

    code = "VENUE_FROZEN"

    def __init__(self, venue_id: str, blocking_order_id: UUID | None) -> None:
        super().__init__(f"venue {venue_id} is frozen by order {blocking_order_id}")
        self.venue_id = venue_id
        self.blocking_order_id = blocking_order_id


class OrderNotFound(LookupError):
    code = "ORDER_NOT_FOUND"


class Session(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Clock(Protocol):
    def utc(self) -> datetime: ...


class AuditAppender(Protocol):
    async def append(self, session: Any, record_type: str, payload: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class Transition:
    order_id: UUID
    venue_id: str
    from_state: OrderState
    to_state: OrderState


class OrderStateMachine:
    """The only writer of `order.status`; one audit record per accepted move."""

    def __init__(self, *, audit: AuditAppender, clock: Clock) -> None:
        self._audit = audit
        self._clock = clock

    async def transition(
        self,
        session: Session,
        order_id: UUID,
        to: OrderState | OrderStatus,
        *,
        correlation_id: str,
        filled_quantity: Decimal | None = None,
    ) -> Transition:
        target = OrderState(to)
        row = await session.fetchrow(_READ_ORDER, order_id)
        if row is None:
            raise OrderNotFound(f"no order {order_id}")
        venue_id = str(row["venue_id"])
        source = OrderState(str(row["status"]))

        if target not in LEGAL_TRANSITIONS[source]:
            await self._record(
                session,
                order_id,
                venue_id,
                status="REFUSED",
                reason=IllegalTransition.code,
                transition=f"{source}->{target}",
                from_status=str(source),
                to_status=str(target),
                correlation_id=correlation_id,
            )
            raise IllegalTransition(order_id, source, target)

        if target is OrderState.UNKNOWN:
            blocking = await session.fetchval(_UNKNOWN_EXISTS, venue_id, order_id)
            if blocking is not None:
                await self._record(
                    session,
                    order_id,
                    venue_id,
                    status="REFUSED",
                    reason=VenueFrozen.code,
                    transition=f"{source}->{target}",
                    from_status=str(source),
                    to_status=str(target),
                    correlation_id=correlation_id,
                    blocking_order_id=str(blocking),
                )
                raise VenueFrozen(venue_id, blocking)

        await session.execute(_UPDATE_STATUS, order_id, str(target), filled_quantity)
        await self._record(
            session,
            order_id,
            venue_id,
            status=str(target),
            transition=f"{source}->{target}",
            from_status=str(source),
            to_status=str(target),
            correlation_id=correlation_id,
        )
        return Transition(order_id, venue_id, source, target)

    async def _record(
        self,
        session: Session,
        order_id: UUID,
        venue_id: str,
        *,
        status: str,
        transition: str,
        from_status: str,
        to_status: str,
        correlation_id: str,
        **extra: Any,
    ) -> None:
        payload = {
            "order_id": str(order_id),
            "venue_id": venue_id,
            "status": status,
            "transition": transition,
            "from_status": from_status,
            "to_status": to_status,
            "correlation_id": correlation_id,
            "at": self._clock.utc().isoformat(),
            **{key: value for key, value in extra.items() if value is not None},
        }
        await self._audit.append(session, AUDIT_ORDER, payload)
