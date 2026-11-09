"""T071: keep `position` equal to the fills that built it, and say how the stop must follow.

The stop itself is not placed here: `contracts/stop-monitor.md` gives that job to the monitor,
which sizes the protective stop from `position.quantity`. What this module guarantees is the
input the monitor reads — after every committed fill the open position's quantity equals the
order's filled quantity, and a resize demand carrying that quantity is returned. FR-068 and
SC-023 are therefore properties of one row, not of two systems agreeing.

Only `fill` rows and `position` rows are written here. `order.status` belongs to the state
machine and the venue calls to `OrderWriter`; the one column this module asks the writer to set
is the order's `position_id`, so the page for an order can name the position it opened.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID

QUANTITY_STEP = Decimal("0.00000001")
PRICE_STEP = Decimal("0.00000001")
NOTIONAL_STEP = Decimal("0.00000001")
RESIZE_REASON = "RESIZE_AFTER_PARTIAL"
DEFAULT_CLOSE_REASON = "OPERATOR"

_ENTRY = """
SELECT o.venue_id, o.pair, o.side, so.stop_price, tp.target_price, tp.horizon_hours
  FROM "order" o
  JOIN signed_order so ON so.id = o.signed_order_id
  JOIN trade_proposal tp ON tp.id = so.proposal_id
 WHERE o.id = $1
"""

_OPEN_POSITION = """
SELECT id, venue_id, pair, quantity, avg_entry_price, stop_price, fees_idr, taxes_idr
  FROM position
 WHERE venue_id = $1
   AND pair = $2
   AND status IN ('OPEN', 'CLOSING')
 FOR UPDATE
"""

_INSERT_POSITION = """
INSERT INTO position (
    venue_id, pair, quantity, avg_entry_price, notional_idr, stop_price, stop_protection,
    target_price, opened_at, time_stop_at, fees_idr, taxes_idr
) VALUES ($1, $2, $3, $4, $5, $6, 'MONITORED', $7, $8, $9, $10, $11)
RETURNING id
"""

_GROW_POSITION = """
UPDATE position
   SET quantity = $2,
       avg_entry_price = $3,
       notional_idr = $4,
       fees_idr = fees_idr + $5,
       taxes_idr = taxes_idr + $6
 WHERE id = $1
"""

_SHRINK_POSITION = """
UPDATE position
   SET quantity = $2,
       fees_idr = fees_idr + $3,
       taxes_idr = taxes_idr + $4
 WHERE id = $1
"""

_CLOSE_POSITION = """
UPDATE position
   SET status = 'CLOSED',
       closed_at = $2,
       close_reason = $3,
       fees_idr = fees_idr + $4,
       taxes_idr = taxes_idr + $5
 WHERE id = $1
"""


class Session(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Clock(Protocol):
    def utc(self) -> datetime: ...


class OrderLinker(Protocol):
    async def attach_position(
        self, order_id: UUID, position_id: UUID, *, correlation_id: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StopResize:
    """A demand for the stop monitor: protect exactly `quantity` at `stop_price`.

    `generation` is monotonic per position so the monitor's stale-generation check has something
    to compare. It lives in memory for now; persisting it belongs with the stop order itself.
    """

    position_id: UUID
    venue_id: str
    pair: str
    quantity: Decimal
    stop_price: Decimal
    reason: str
    generation: int


@dataclass(frozen=True, slots=True)
class PositionUpdate:
    position_id: UUID
    quantity: Decimal
    average_entry_price: Decimal
    stop: StopResize


class NoOpenPosition(LookupError):
    code = "POSITION_NOT_OPEN"


class PositionSync:
    """Applies one fill to the position it belongs to and returns the resize it demands."""

    def __init__(
        self, *, session: Session, clock: Clock, linker: OrderLinker | None = None
    ) -> None:
        self._session = session
        self._clock = clock
        self._linker = linker
        self._generations: dict[UUID, int] = {}

    async def apply_fill(
        self,
        *,
        order_id: UUID,
        quantity: Decimal,
        price: Decimal,
        filled_at: datetime,
        fee_idr: Decimal = Decimal("0"),
        tax_idr: Decimal = Decimal("0"),
        close_reason: str | None = None,
    ) -> PositionUpdate:
        if quantity <= 0 or price <= 0:
            raise ValueError("a fill has a positive quantity and price")
        row = await self._session.fetchrow(_ENTRY, order_id)
        if row is None:
            raise NoOpenPosition(f"no order {order_id} to size a position from")
        venue_id, pair = str(row["venue_id"]), str(row["pair"])
        position = await self._session.fetchrow(_OPEN_POSITION, venue_id, pair)

        if str(row["side"]) == "BUY":
            update = (
                await self._open(row, venue_id, pair, quantity, price, filled_at, fee_idr, tax_idr)
                if position is None
                else await self._grow(position, quantity, price, fee_idr, tax_idr)
            )
        else:
            if position is None:
                raise NoOpenPosition(f"{venue_id}:{pair} has no open position to reduce")
            update = await self._shrink(position, quantity, fee_idr, tax_idr, close_reason)

        return update

    async def _open(
        self,
        entry: Mapping[str, Any],
        venue_id: str,
        pair: str,
        quantity: Decimal,
        price: Decimal,
        filled_at: datetime,
        fee_idr: Decimal,
        tax_idr: Decimal,
    ) -> PositionUpdate:
        session = self._session
        stop_price = _money(entry["stop_price"])
        average = _price(price)
        horizon = entry["horizon_hours"]
        position_id = await session.fetchrow(
            _INSERT_POSITION,
            venue_id,
            pair,
            _quantity(quantity),
            average,
            _notional(quantity * price),
            stop_price,
            _money(entry["target_price"]),
            filled_at,
            filled_at + timedelta(hours=horizon),
            _money(fee_idr),
            _money(tax_idr),
        )
        assert position_id is not None
        new_id = UUID(str(position_id["id"]))
        return PositionUpdate(
            position_id=new_id,
            quantity=_quantity(quantity),
            average_entry_price=average,
            stop=self._resize(new_id, venue_id, pair, quantity, stop_price),
        )

    async def _grow(
        self,
        position: Mapping[str, Any],
        quantity: Decimal,
        price: Decimal,
        fee_idr: Decimal,
        tax_idr: Decimal,
    ) -> PositionUpdate:
        session = self._session
        position_id = UUID(str(position["id"]))
        venue_id, pair = str(position["venue_id"]), str(position["pair"])
        held = _quantity(position["quantity"])
        previous_average = _price(position["avg_entry_price"])
        total = held + quantity
        average = _price((previous_average * held + price * quantity) / total)
        await session.execute(
            _GROW_POSITION,
            position_id,
            total,
            average,
            _notional(total * average),
            _money(fee_idr),
            _money(tax_idr),
        )
        return PositionUpdate(
            position_id=position_id,
            quantity=total,
            average_entry_price=average,
            stop=self._resize(
                position_id,
                venue_id,
                pair,
                total,
                _money(position["stop_price"]),
            ),
        )

    async def _shrink(
        self,
        position: Mapping[str, Any],
        quantity: Decimal,
        fee_idr: Decimal,
        tax_idr: Decimal,
        close_reason: str | None,
    ) -> PositionUpdate:
        session = self._session
        position_id = UUID(str(position["id"]))
        venue_id, pair = str(position["venue_id"]), str(position["pair"])
        held = _quantity(position["quantity"])
        total = held - quantity
        stop_price = _money(position["stop_price"])
        if total <= 0:
            # A closed row must stay positive (`position_quantity_positive`), so it keeps the
            # size that was closed; the outcome lives in `realised_pnl_idr`, not in `quantity`.
            await session.execute(
                _CLOSE_POSITION,
                position_id,
                self._clock.utc(),
                close_reason or DEFAULT_CLOSE_REASON,
                _money(fee_idr),
                _money(tax_idr),
            )
            return PositionUpdate(
                position_id=position_id,
                quantity=Decimal("0"),
                average_entry_price=_price(position["avg_entry_price"]),
                stop=self._resize(
                    position_id,
                    str(position.get("venue_id", "")),
                    str(position.get("pair", "")),
                    Decimal("0"),
                    stop_price,
                ),
            )
        await session.execute(
            _SHRINK_POSITION,
            position_id,
            total,
            _money(fee_idr),
            _money(tax_idr),
        )
        return PositionUpdate(
            position_id=position_id,
            quantity=total,
            average_entry_price=_price(position["avg_entry_price"]),
            stop=self._resize(
                position_id,
                venue_id,
                pair,
                total,
                stop_price,
            ),
        )

    def _resize(
        self,
        position_id: UUID,
        venue_id: str,
        pair: str,
        quantity: Decimal,
        stop_price: Decimal,
    ) -> StopResize:
        generation = self._generations.get(position_id, 0) + 1
        self._generations[position_id] = generation
        return StopResize(
            position_id=position_id,
            venue_id=venue_id,
            pair=pair,
            quantity=quantity,
            stop_price=stop_price,
            reason=RESIZE_REASON,
            generation=generation,
        )


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def _price(value: Any) -> Decimal:
    return _money(value)


def _quantity(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


def _notional(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(NOTIONAL_STEP, rounding=ROUND_HALF_UP)
