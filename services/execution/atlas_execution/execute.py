"""T087: the protective-stop acceptance path (FR-063, contracts/decision-risk.md §6).

A stop is not an entry: it carries no verdict and needs no mode gate, because it only ever
reduces an exposure the system already holds. What it does need is an order of operations that
makes a naked position loud:

1. a stop that is already live is cancelled before the tighter one is placed, so two reduce-only
   sells can never rest against the same quantity;
2. the placement is attempted and its failure is not swallowed — an adopted position that ends
   up without a stop halts the system in the same transaction;
3. only a confirmed venue ack moves ``position.stop_price`` and its generation forward, so a
   crash between the venue and the database replays rather than diverges.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from atlas_schemas.order import ProtectiveStopRequest
from atlas_schemas.venue import VenueUnavailable

ProtectiveStopRefusal = Literal[
    "POSITION_NOT_OPEN",
    "QUANTITY_EXCEEDS_POSITION",
    "STOP_NOT_TIGHTENING",
    "STALE_GENERATION",
    "STOP_ABOVE_MARKET",
    "PROTECTIVE_STOP_NOT_PLACED",
]


@dataclass(frozen=True, slots=True)
class Accepted:
    # A locally armed stop has no order row, so it has no id.
    order_id: UUID | None
    client_order_id: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class Rejected:
    code: ProtectiveStopRefusal
    detail: str = ""


class StopWriter(Protocol):
    def native_stop_orders(self) -> bool: ...

    async def cancel(
        self,
        order_id: UUID,
        *,
        correlation_id: str,
        reason: str,
        verdict_expires_at: datetime | None,
    ) -> Any: ...

    async def place_protective(
        self,
        *,
        position_id: UUID,
        pair: str,
        quantity: Any,
        limit_price: Any,
        client_order_id: str,
        correlation_id: str,
    ) -> Any: ...


class Session(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


class AuditAppender(Protocol):
    async def append(self, session: Any, record_type: str, payload: Mapping[str, Any]) -> Any: ...


class Clock(Protocol):
    def utc(self) -> datetime: ...


_POSITION = """
SELECT id, venue_id, pair, quantity, stop_price, stop_protection, stop_order_id,
       stop_generation, adopted, status
  FROM position
 WHERE id = $1
 FOR UPDATE
"""

_MARK_UNCONFIRMED = """
UPDATE position
   SET stop_confirmation = 'UNCONFIRMED', stop_unconfirmed_at = $2, stop_order_id = NULL
 WHERE id = $1
"""

_SET_STOP = """
UPDATE position
   SET stop_price = $2,
       stop_order_id = $3,
       stop_protection = $4,
       stop_generation = $5,
       stop_confirmation = 'CONFIRMED',
       stop_unconfirmed_at = NULL
 WHERE id = $1
"""

_CANCEL_LIVE_STOP = """
UPDATE "order" SET status = 'CANCELED', updated_at = now() WHERE id = $1
"""

_MODE_HEAD = "SELECT mode, seq FROM mode_state ORDER BY seq DESC LIMIT 1"

_LAST_PRICE = "SELECT last FROM quote_snapshot WHERE venue_id = $1 AND pair = $2"

_HALT = """
INSERT INTO mode_state (mode, previous_mode, reason_code, actor)
SELECT 'HALTED', head.mode, 'AUTO_DEMOTE', 'SYSTEM'
  FROM mode_state AS head
 ORDER BY head.seq DESC
 LIMIT 1
"""

_INCIDENT = """
INSERT INTO incident (class, kind, severity, auto_action, root_cause, detail)
VALUES ('SAFETY', $1, 'SEV1', 'HALTED', $2, CAST($3 AS jsonb))
"""


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class StopAcceptance:
    def __init__(
        self,
        *,
        session: Session,
        writer: StopWriter,
        audit: AuditAppender,
        clock: Clock,
    ) -> None:
        self._session = session
        self._writer = writer
        self._audit = audit
        self._clock = clock

    async def accept(
        self, request: ProtectiveStopRequest, *, correlation_id: str
    ) -> Accepted | Rejected:
        """Place, tighten, or replay the stop a monitor asked for. Never raises for a refusal."""
        session = self._session
        position = await session.fetchrow(_POSITION, request.position_id)
        if position is None or position["status"] not in ("OPEN", "CLOSING"):
            return Rejected("POSITION_NOT_OPEN", "no open position holds that id")
        if position["pair"] != request.pair or position["venue_id"] != request.venue_id:
            # A monitor naming another pair is a wiring error, not a refusal to record.
            raise ValueError(
                f"request targets {request.venue_id}/{request.pair}, position is "
                f"{position['venue_id']}/{position['pair']}"
            )
        live = await self._live_stop(request.position_id)

        if request.quantity > position["quantity"]:
            # A stop larger than the position could sell more than is held.
            return Rejected(
                "QUANTITY_EXCEEDS_POSITION",
                f"asked {request.quantity}, position holds {position['quantity']}",
            )

        if int(request.generation) <= int(position["stop_generation"]):
            if (
                live is not None
                and int(request.generation) == int(position["stop_generation"])
                and Decimal(live["limit_price"]) == request.stop_price
            ):
                return Accepted(live["id"], str(live["client_order_id"]), replayed=True)
            if (
                live is None
                and int(request.generation) == int(position["stop_generation"])
                and Decimal(position["stop_price"]) == request.stop_price
            ):
                return Accepted(
                    None,
                    f"atlas-stop-{str(request.position_id)[:8]}-{request.generation}",
                    replayed=True,
                )
            return Rejected(
                "STALE_GENERATION",
                f"generation {request.generation} does not advance {position['stop_generation']}",
            )

        recorded_stop = Decimal(position["stop_price"])
        if request.stop_price < recorded_stop:
            await self._audit.append(
                session,
                "ORDER",
                {
                    "position_id": str(request.position_id),
                    "request_id": str(request.request_id),
                    "status": "REFUSED",
                    "refusal": "STOP_NOT_TIGHTENING",
                    "requested_stop_price": str(request.stop_price),
                    "recorded_stop_price": str(position["stop_price"]),
                    "correlation_id": correlation_id,
                },
            )
            return Rejected(
                "STOP_NOT_TIGHTENING",
                f"stop {request.stop_price} is wider than the recorded {position['stop_price']}",
            )

        last_price = await session.fetchval(_LAST_PRICE, position["venue_id"], position["pair"])
        if last_price is not None and request.stop_price >= Decimal(last_price):
            await self._audit.append(
                session,
                "ORDER",
                {
                    "position_id": str(request.position_id),
                    "request_id": str(request.request_id),
                    "status": "REFUSED",
                    "refusal": "STOP_ABOVE_MARKET",
                    "requested_stop_price": str(request.stop_price),
                    "last_price": str(last_price),
                    "correlation_id": correlation_id,
                },
            )
            return Rejected(
                "STOP_ABOVE_MARKET",
                f"stop {request.stop_price} is at or above the last price {last_price}",
            )

        client_order_id = f"atlas-stop-{str(request.position_id)[:8]}-{request.generation}"

        if live is not None:
            # Cancel first: while both rest, the pair could sell twice the quantity held.
            await self._writer.cancel(
                live["id"],
                correlation_id=correlation_id,
                reason="superseded_by_tighter_stop",
                verdict_expires_at=None,
            )
            await session.execute(_CANCEL_LIVE_STOP, live["id"])

        if not self._writer.native_stop_orders():
            # Indodax has no native stop: a SELL LIMIT at the stop price would rest *below* the
            # market and fill on arrival. Arm it locally; the monitor enforces it.
            await session.execute(
                _SET_STOP,
                request.position_id,
                request.stop_price,
                None,
                "MONITORED",
                request.generation,
            )
            await self._audit.append(
                session,
                "ORDER",
                {
                    "position_id": str(request.position_id),
                    "request_id": str(request.request_id),
                    "status": "ARMED",
                    "stop_protection": "MONITORED",
                    "stop_price": str(request.stop_price),
                    "generation": int(request.generation),
                    "correlation_id": correlation_id,
                },
            )
            return Accepted(None, client_order_id)

        try:
            placed = await self._writer.place_protective(
                position_id=request.position_id,
                pair=position["pair"],
                quantity=request.quantity,
                limit_price=request.stop_price,
                client_order_id=client_order_id,
                correlation_id=correlation_id,
            )
        except VenueUnavailable as exc:
            await self._unprotected(position, request, correlation_id, str(exc))
            return Rejected("PROTECTIVE_STOP_NOT_PLACED", str(exc))

        await session.execute(
            _SET_STOP,
            request.position_id,
            request.stop_price,
            placed.order_id,
            # The stop now rests at the venue, so the venue is what enforces it.
            "NATIVE_ORDER",
            request.generation,
        )
        return Accepted(placed.order_id, client_order_id)

    async def _live_stop(self, position_id: UUID) -> Mapping[str, Any] | None:
        return await self._session.fetchrow(
            """
            SELECT o.id, o.client_order_id, o.venue_order_id, o.limit_price, o.status
              FROM "order" o
             WHERE o.position_id = $1 AND o.is_protective AND o.side = 'SELL'
               AND o.status IN ('OPEN', 'PARTIALLY_FILLED', 'UNKNOWN')
             ORDER BY o.created_at DESC
             LIMIT 1
            """,
            position_id,
        )

    async def _unprotected(
        self,
        position: Mapping[str, Any],
        request: ProtectiveStopRequest,
        correlation_id: str,
        detail: str,
    ) -> None:
        """No stop reached the venue. An adopted position cannot be left like that."""
        session = self._session
        await session.execute(_MARK_UNCONFIRMED, request.position_id, self._clock.utc())
        await self._audit.append(
            session,
            "ORDER",
            {
                "position_id": str(request.position_id),
                "request_id": str(request.request_id),
                "status": "REFUSED",
                "refusal": "PROTECTIVE_STOP_NOT_PLACED",
                "detail": detail,
                "correlation_id": correlation_id,
            },
        )
        if not position["adopted"]:
            return

        head = await session.fetchrow(_MODE_HEAD)
        if head is not None and head["mode"] != "HALTED":
            await session.execute(_HALT)
        await session.execute(
            _INCIDENT,
            "ADOPTED_POSITION_UNPROTECTED",
            "an adopted position could not be given a stop",
            _json(
                {
                    "position_id": str(request.position_id),
                    "correlation_id": correlation_id,
                    "detail": detail,
                }
            ),
        )
        await self._audit.append(
            session,
            "INCIDENT",
            {
                "kind": "ADOPTED_POSITION_UNPROTECTED",
                "class": "SAFETY",
                "severity": "SEV1",
                "auto_action": "HALTED",
                "position_id": str(request.position_id),
                "correlation_id": correlation_id,
                "at": self._clock.utc().isoformat(),
            },
        )
