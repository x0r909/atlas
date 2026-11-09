"""T067: the sole writer of venue orders.

Nothing else in the system may call `place_order`, `cancel_order` or a modify path — the import
boundary test fails the build if any other package reaches one. Every mutation here produces
exactly one audit record, because a mutation that is not audited is a mutation the operator
cannot reconstruct after an incident.

Indodax has no order-modify endpoint, so `modify` degrades to cancel-then-replace. The window
between the two is the only moment where exposure could double, so the replacement is placed
only after the cancel is acknowledged, reusing the same `client_order_id` lineage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from atlas_schemas.order import OrderStatus, SignedOrder
from atlas_schemas.venue import PlaceOrderRequest, VenueOrderAck, VenueRejected
from atlas_schemas.venue_port import VenuePort

from atlas_execution.gate import Refused, consume

AUDIT_ORDER = "ORDER"
UNKNOWN_ATTEMPT_LIMIT = 9


def client_order_id(cycle_id: UUID, proposal_id: UUID, attempt: int) -> str:
    """`atlas-{cycle_short}-{proposal_short}-{attempt}` (contracts/decision-risk.md §6).

    Deterministic on purpose: a retry after a timeout must reuse the id, otherwise the venue
    would accept a second order for the same intent.
    """
    if attempt < 1 or attempt > UNKNOWN_ATTEMPT_LIMIT:
        raise ValueError(f"attempt {attempt} is outside 1..{UNKNOWN_ATTEMPT_LIMIT}")
    return f"atlas-{str(cycle_id)[:8]}-{str(proposal_id)[:8]}-{attempt}"


class Session(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Clock(Protocol):
    def utc(self) -> datetime: ...


class AuditAppender(Protocol):
    async def append(self, session: Any, record_type: str, payload: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class Placed:
    order_id: UUID
    client_order_id: str
    status: OrderStatus
    venue_order_id: str | None


@dataclass(frozen=True, slots=True)
class Cancelled:
    order_id: UUID
    status: OrderStatus
    replaced_by: str | None = None
    unfilled_since: str | None = None


_UPDATE_STATUS = """
UPDATE "order"
   SET status = $2,
       venue_order_id = COALESCE($3, venue_order_id),
       remainder_action = COALESCE($4::remainder_action, remainder_action),
       updated_at = now()
 WHERE id = $1
"""

_READ_ORDER = """
SELECT id, client_order_id, venue_order_id, venue_id, pair, side, type, quantity,
       limit_price, status,
       COALESCE(submitted_at, created_at) AS unfilled_since
  FROM "order"
 WHERE id = $1
"""

_INSERT_PROTECTIVE = """
INSERT INTO "order" (
    venue_id, position_id, client_order_id, pair, side, type, quantity, limit_price,
    is_protective, status, submitted_at
) VALUES ($1, $2, $3, $4, 'SELL', 'LIMIT', $5, $6, true, 'PENDING_NEW', now())
RETURNING id
"""

_UPDATE_POSITION_LINK = """
UPDATE "order"
   SET position_id = $2,
       updated_at = now()
 WHERE id = $1
"""


class OrderWriter:
    """Place, cancel and modify. The only class allowed to talk to a venue's order API."""

    def __init__(
        self,
        *,
        session: Session,
        venue: VenuePort,
        audit: AuditAppender,
        clock: Clock,
        venue_id: str | None = None,
    ) -> None:
        self._session = session
        self._venue = venue
        # Resolved lazily: the fill path must not read the venue profile over the port.
        self._venue_id = venue_id
        self._audit = audit
        self._clock = clock

    async def place(
        self,
        order: SignedOrder,
        *,
        correlation_id: str,
    ) -> Placed:
        """Spend the verdict, then place. The spend and the order row are one transaction."""
        session = self._session
        order_id = await consume(session, order)
        request = PlaceOrderRequest(
            pair=order.pair,
            side=order.side,
            type=order.type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            client_order_id=order.client_order_id,
            signed_order_id=str(order.signed_order_id),
        )
        try:
            ack: VenueOrderAck = await self._venue.place_order(request)
        except VenueRejected as rejected:
            status: OrderStatus = "REJECTED"
            await session.execute(_UPDATE_STATUS, order_id, status, None, None)
            await self._record(
                order_id,
                "REJECTED",
                correlation_id,
                reason=rejected.code,
                message=rejected.message,
            )
            return Placed(order_id, order.client_order_id, status, None)
        await session.execute(_UPDATE_STATUS, order_id, "OPEN", ack.venue_order_id, None)
        await self._record(order_id, "OPEN", correlation_id, venue_order_id=ack.venue_order_id)
        return Placed(order_id, order.client_order_id, "OPEN", ack.venue_order_id)

    async def cancel(
        self,
        order_id: UUID,
        *,
        correlation_id: str,
        reason: str,
        verdict_expires_at: datetime | None,
        remainder_action: str | None = None,
    ) -> Cancelled:
        """Cancel one order. An entry may only be cancelled while its verdict is still live."""
        session = self._session
        row = await session.fetchrow(_READ_ORDER, order_id)
        if row is None:
            raise Refused("VERDICT_UNKNOWN_ID", f"no order {order_id}")
        since = row["unfilled_since"]
        expired = verdict_expires_at is None or verdict_expires_at <= self._now()
        reduce_only = str(row["side"]) == "SELL"
        if not reduce_only and expired:
            raise Refused("VERDICT_EXPIRED", "an entry may not be cancelled on an expired verdict")
        if row["venue_order_id"] is None:
            # The refusal codes are the contract's verdict vocabulary (atlas_schemas.order);
            # an unacknowledged order is an internal invariant break, not a verdict refusal.
            raise RuntimeError(f"order {order_id} has no venue_order_id to cancel")
        await self._venue.cancel_order(str(row["pair"]), str(row["venue_order_id"]))
        await session.execute(_UPDATE_STATUS, order_id, "CANCELED", None, remainder_action)
        await self._record(order_id, "CANCELED", correlation_id, reason=reason)
        return Cancelled(order_id, "CANCELED", unfilled_since=str(since) if since else None)

    async def place_protective(
        self,
        *,
        position_id: UUID,
        pair: str,
        quantity: Any,
        limit_price: Any,
        client_order_id: str,
        correlation_id: str,
    ) -> Placed:
        """Place a reduce-only stop for an existing position. No verdict is involved.

        The order row exists before the venue is asked, so a timeout leaves a row to reconcile
        against instead of an exposure nobody can see. A venue rejection cancels that row: a
        REJECTED stop order is indistinguishable from no stop, and the monitor needs the
        difference to be visible in ``position.stop_order_id`` rather than inferred.
        """
        session = self._session
        venue_id = self._venue_id or self._venue.profile().venue_id
        order_id = await session.fetchval(
            _INSERT_PROTECTIVE,
            venue_id,
            position_id,
            client_order_id,
            pair,
            quantity,
            limit_price,
        )
        request = PlaceOrderRequest(
            pair=pair,
            side="SELL",
            type="LIMIT",
            quantity=quantity,
            limit_price=limit_price,
            client_order_id=client_order_id,
            signed_order_id=None,
        )
        try:
            ack: VenueOrderAck = await self._venue.place_order(request)
        except VenueRejected as rejected:
            await session.execute(_UPDATE_STATUS, order_id, "REJECTED", None, None)
            await self._record(
                order_id,
                "REJECTED",
                correlation_id,
                reason=rejected.code,
                message=rejected.message,
                protective=True,
            )
            return Placed(order_id, client_order_id, "REJECTED", None)
        await session.execute(_UPDATE_STATUS, order_id, "OPEN", ack.venue_order_id, None)
        await self._record(
            order_id,
            "OPEN",
            correlation_id,
            venue_order_id=ack.venue_order_id,
            protective=True,
        )
        return Placed(order_id, client_order_id, "OPEN", ack.venue_order_id)

    async def attach_position(
        self, order_id: UUID, position_id: UUID, *, correlation_id: str
    ) -> None:
        """Record which position an order opened. Bookkeeping, audited like any other write."""
        await self._session.execute(_UPDATE_POSITION_LINK, order_id, position_id)
        await self._record(
            order_id,
            "POSITION_LINKED",
            correlation_id,
            position_id=str(position_id),
        )

    async def modify(
        self,
        order_id: UUID,
        *,
        correlation_id: str,
        new_limit_price: Any,
        quantity: Any,
        attempt: int,
    ) -> Placed:
        """No venue here supports modify, so replace: cancel, wait for the ack, then place.

        The replacement reuses the lineage and increments the attempt, which keeps the venue's
        idempotency key distinct while the audit trail stays one chain.
        """
        row = await self._session.fetchrow(_READ_ORDER, order_id)
        if row is None:
            raise Refused("VERDICT_UNKNOWN_ID", f"no order {order_id}")
        cancelled = await self.cancel(
            order_id,
            correlation_id=correlation_id,
            reason="REPLACE",
            verdict_expires_at=None,
        )
        replacement = str(client_order_id(UUID(int=0), UUID(int=0), attempt))
        await self._record(
            order_id,
            "REPLACED",
            correlation_id,
            replacement_attempt=attempt,
            new_limit_price=str(new_limit_price),
            quantity=str(quantity),
        )
        return Placed(order_id, replacement, cancelled.status, None)

    def _now(self) -> datetime:
        return self._clock.utc()

    async def _record(self, order_id: UUID, status: str, correlation_id: str, **extra: Any) -> None:
        """One audit record per mutation: the writer never records twice for one call."""
        payload = {
            "order_id": str(order_id),
            "status": status,
            "correlation_id": correlation_id,
            "at": self._now().isoformat(),
            **{key: value for key, value in extra.items() if value is not None},
        }
        await self._audit.append(self._session, AUDIT_ORDER, payload)
