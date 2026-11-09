"""T070: execution reports become fills, and a partial fill never loses its stop.

An exchange reports what happened; this module makes the books agree with it. Three things
happen in one caller-owned transaction, in an order that keeps SC-023 true at every commit
point:

1. the fill is stored under `(order_id, venue_trade_id)`, so a replayed report is recognised
   by the database rather than by a de-duplicating set in memory;
2. the position is opened, grown or reduced to the *new* filled quantity, and the resize the
   stop monitor needs is returned — the position never commits ahead of a stop that matches it;
3. the order moves through the state machine, which is the only writer of `status`,
   `filled_quantity` and `avg_fill_price`.

The unfilled remainder of a partial entry is cancelled after `partial_fill_timeout_s` (45 s,
FR-068). `expire_remainders` is the sweep that does it; the venue call still goes through
`OrderWriter`, so there is exactly one module holding write credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID

from atlas_execution.position_sync import PositionSync, StopResize
from atlas_execution.state_machine import OrderNotFound, OrderState, OrderStateMachine

PARTIAL_FILL_TIMEOUT_S = 45

_ORDER = """
SELECT id, venue_id, pair, side, status, quantity, filled_quantity, avg_fill_price,
       venue_order_id, position_id,
       COALESCE(submitted_at, created_at) AS unfilled_since
  FROM "order"
 WHERE id = $1
 FOR UPDATE
"""

_VERDICT_EXPIRY = """
SELECT COALESCE(v.expires_at, so.expires_at) AS expires_at
  FROM "order" o
  LEFT JOIN signed_order so ON so.id = o.signed_order_id
  LEFT JOIN risk_verdict v ON v.id = so.verdict_id
 WHERE o.id = $1
"""

_INSERT_FILL = """
INSERT INTO fill (order_id, venue_id, venue_trade_id, quantity, price, fee_idr,
                  tax_idr, is_maker, filled_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (venue_id, venue_trade_id) DO NOTHING
RETURNING id
"""

_REMAINDERS = """
SELECT o.id, o.filled_quantity, MIN(f.filled_at) AS first_fill
  FROM "order" o
  JOIN fill f ON f.order_id = o.id
 WHERE o.remainder_action = 'CANCEL_PENDING'
   AND o.status = 'PARTIALLY_FILLED'
 GROUP BY o.id, o.filled_quantity
"""


class Session(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Clock(Protocol):
    def utc(self) -> datetime: ...


class OrderWriterPort(Protocol):
    """The two writer calls this module makes: cancel the remainder, name the position."""

    async def cancel(
        self,
        order_id: UUID,
        *,
        correlation_id: str,
        reason: str,
        verdict_expires_at: datetime | None,
        remainder_action: str | None = ...,
    ) -> Any: ...

    async def attach_position(
        self, order_id: UUID, position_id: UUID, *, correlation_id: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution report. `venue_trade_id` is the venue's idempotency key for it."""

    order_id: UUID
    venue_trade_id: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    fee_idr: Decimal = Decimal("0")
    tax_idr: Decimal = Decimal("0")
    is_maker: bool = False


@dataclass(frozen=True, slots=True)
class FillRecorded:
    fill_id: UUID | None
    order_id: UUID
    status: OrderState
    filled_quantity: Decimal
    average_fill_price: Decimal
    stop: StopResize | None
    remainder_due_at: datetime | None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RemainderCancelled:
    order_id: UUID
    cancelled: bool


class Overfill(Exception):
    """A report that would fill more than the order asked for. Never written."""

    code = "OVERFILL"


class FillHandler:
    """Turns execution reports into fills, position updates and stop resizes."""

    def __init__(
        self,
        *,
        session: Session,
        machine: OrderStateMachine,
        writer: OrderWriterPort,
        positions: PositionSync,
        clock: Clock,
        timeout_s: int = PARTIAL_FILL_TIMEOUT_S,
        correlation_id_prefix: str = "fill",
    ) -> None:
        self._session = session
        self._machine = machine
        self._writer = writer
        self._positions = positions
        self._clock = clock
        self._timeout = timedelta(seconds=timeout_s)
        self._prefix = correlation_id_prefix

    async def record(self, fill: Fill, *, correlation_id: str) -> FillRecorded:
        """Record one fill. The caller owns the transaction, so a refusal rolls back whole."""
        session = self._session
        if fill.quantity <= 0 or fill.price <= 0:
            raise ValueError("a fill has a positive quantity and price")
        row = await session.fetchrow(_ORDER, fill.order_id)
        if row is None:
            raise OrderNotFound(f"no order {fill.order_id}")

        fill_id = await session.fetchrow(
            _INSERT_FILL,
            fill.order_id,
            str(row["venue_id"]),
            fill.venue_trade_id,
            fill.quantity,
            fill.price,
            fill.fee_idr,
            fill.tax_idr,
            fill.is_maker,
            fill.filled_at,
        )
        if fill_id is None:
            return FillRecorded(
                fill_id=None,
                order_id=fill.order_id,
                status=OrderState(str(row["status"])),
                filled_quantity=_decimal(row["filled_quantity"]),
                average_fill_price=_decimal(row["avg_fill_price"] or 0),
                stop=None,
                remainder_due_at=None,
                duplicate=True,
            )

        ordered = _decimal(row["quantity"])
        previous = _decimal(row["filled_quantity"])
        total = previous + fill.quantity
        if total > ordered:
            raise Overfill(f"{fill.order_id}: {total} filled against an order for {ordered}")
        average = _price(
            (_decimal(row["avg_fill_price"] or 0) * previous + fill.price * fill.quantity) / total
        )
        target = OrderState.FILLED if total == ordered else OrderState.PARTIALLY_FILLED
        remainder = "CANCEL_PENDING" if target is OrderState.PARTIALLY_FILLED else "NONE"

        update = await self._positions.apply_fill(
            order_id=fill.order_id,
            quantity=fill.quantity,
            price=fill.price,
            filled_at=fill.filled_at,
            fee_idr=fill.fee_idr,
            tax_idr=fill.tax_idr,
        )
        if row["position_id"] is None and str(row["side"]) == "BUY":
            await self._writer.attach_position(
                fill.order_id, update.position_id, correlation_id=correlation_id
            )

        await self._machine.transition(
            session,
            fill.order_id,
            target,
            correlation_id=correlation_id,
            filled_quantity=total,
            avg_fill_price=average,
            fee_idr=fill.fee_idr,
            tax_idr=fill.tax_idr,
            remainder_action=remainder,
        )

        due = (
            (_as_datetime(row["unfilled_since"]) or fill.filled_at) + self._timeout
            if target is OrderState.PARTIALLY_FILLED
            else None
        )
        return FillRecorded(
            fill_id=UUID(str(fill_id["id"])),
            order_id=fill.order_id,
            status=target,
            filled_quantity=total,
            average_fill_price=average,
            stop=update.stop,
            remainder_due_at=due,
        )

    async def expire_remainders(
        self, *, correlation_id: str, now: datetime | None = None
    ) -> list[RemainderCancelled]:
        """Cancel the unfilled part of every partial entry older than the timeout.

        A second sweep finds `remainder_action = 'CANCELED'` and does nothing, so the venue sees
        exactly one cancel per order even when two sweeps overlap on the same clock tick.
        """
        session = self._session
        moment = now or self._clock.utc()
        rows = await session.fetch(_REMAINDERS)
        cancelled: list[RemainderCancelled] = []
        for row in rows:
            first_fill = _as_datetime(row["first_fill"])
            if first_fill is None or moment < first_fill + self._timeout:
                continue
            order_id = UUID(str(row["id"]))
            expiry = await session.fetchrow(_VERDICT_EXPIRY, order_id)
            await self._writer.cancel(
                order_id,
                correlation_id=f"{self._prefix}-remainder",
                reason="PARTIAL_FILL_TIMEOUT",
                verdict_expires_at=_as_datetime(expiry["expires_at"]) if expiry else None,
                remainder_action="CANCELED",
            )
            cancelled.append(RemainderCancelled(order_id=order_id, cancelled=True))
        return cancelled


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def _price(value: Any) -> Decimal:
    return _decimal(value).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
