"""T086: the protective-stop acceptance path (FR-063, contracts/decision-risk.md §6).

Authored against the path's contract, not its implementation: the four DB-backed refusals, one
placement in a single cycle when the stop is missing, and the safety rule that an adopted
position with no stop forces HALTED instead of being left naked.

The cycle below stands in for T084's monitor loop: it finds an open position with no live
protective order, asks the acceptance path to place the stop, and counts what reached the
venue. T084 replaces the stand-in, not the contract these tests pin.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from atlas_common import audit_chain
from atlas_execution.execute import Accepted, Rejected, StopAcceptance
from atlas_execution.orders import OrderWriter
from atlas_execution.venues import indodax_facts as facts
from atlas_schemas.order import ProtectiveStopRequest
from atlas_schemas.venue import PlaceOrderRequest, VenueOrderAck, VenueProfile, VenueUnavailable

DSN = os.environ.get("ATLAS_DB_DSN")
VENUE = "indodax"


class DbAudit:
    async def append(
        self, session: Any, record_type: str, payload: Mapping[str, Any]
    ) -> tuple[int, str]:
        return await audit_chain.append(session, record_type, payload)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def utc(self) -> datetime:
        return self.at


class StopVenue:
    """Places and cancels; every call is counted so "no orders emitted" means something."""

    def __init__(self, *, venue_id: str = VENUE, native_stop_orders: bool = False) -> None:
        self.placed: list[PlaceOrderRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self.fail_with: str | None = None
        self._profile = VenueProfile(
            venue_id=venue_id,
            version=1,
            fee_maker_pct=facts.FEE_MAKER_PCT,
            fee_taker_pct=facts.FEE_TAKER_PCT,
            cfx_fee_pct=None,
            cfx_fee_pct_assumed=facts.CFX_FEE_PCT_ASSUMED,
            tax_buy_pct_assumed=facts.TAX_BUY_PCT_ASSUMED,
            tax_pph_pct=facts.TAX_PPH_PCT,
            tax_ppn_pct=facts.TAX_PPN_PCT,
            tax_applies_to="SELL_ONLY",
            min_notional_idr=facts.MIN_NOTIONAL_IDR,
            max_leverage=1,
            native_stop_orders=native_stop_orders,
            cost_model_version="1",
        )

    def profile(self) -> VenueProfile:
        return self._profile

    async def place_order(self, request: PlaceOrderRequest) -> VenueOrderAck:
        self.placed.append(request)
        if self.fail_with == "unavailable":
            raise VenueUnavailable("simulated stop placement timeout")
        return VenueOrderAck(
            pair=request.pair,
            venue_order_id=f"venue-{uuid4().hex[:12]}",
            client_order_id=request.client_order_id,
            status="OPEN",
            venue_ts=0,
        )

    async def cancel_order(self, pair: str, venue_order_id: str) -> VenueOrderAck:
        self.cancelled.append((pair, venue_order_id))
        return VenueOrderAck(
            pair=pair,
            venue_order_id=venue_order_id,
            client_order_id="client-1",
            status="CANCELED",
            venue_ts=0,
        )


@pytest.fixture()
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    if not DSN:
        pytest.skip("ATLAS_DB_DSN not set")
    created = await asyncpg.create_pool(DSN)
    async with created.acquire() as connection:
        for table in ("order", "position", "venue", "incident", "mode_state"):
            if await connection.fetchval("SELECT to_regclass($1)", f"public.{table}") is None:
                await created.close()
                pytest.skip(f"{table} does not exist; run `alembic upgrade head` first")
    try:
        yield created
    finally:
        await created.close()


_VENUE_ROW = """
INSERT INTO venue (id, display_name, regulator, enabled_for_live, api_base)
VALUES ($1, $2, 'NONE', false, 'https://example.invalid')
"""

_HEAD = "SELECT mode FROM mode_state ORDER BY seq DESC LIMIT 1"

_STARTUP = (
    "INSERT INTO mode_state (mode, reason_code, actor)"
    " VALUES ('PAPER', 'STARTUP_DEFAULT', 'SYSTEM')"
)

_LIVE_STOP_ROW = """
INSERT INTO "order" (
    venue_id, position_id, client_order_id, venue_order_id, pair, side, type,
    quantity, limit_price, is_protective, status, submitted_at
) VALUES ($1, $2, $3, $4, 'BTCIDR', 'SELL', 'LIMIT', $5, $6, true, 'OPEN', now())
RETURNING id
"""

_POSITION = """
INSERT INTO position (
    id, venue_id, pair, quantity, avg_entry_price, notional_idr, stop_price,
    stop_protection, target_price, time_stop_at, adopted
) VALUES ($1, $2, 'BTCIDR', $3, 1000, 800000, $4, $5, 1200, now() + interval '24 hours', $6)
"""


class Seed:
    """One venue and one open position whose stop the acceptance path is asked to place."""

    def __init__(self, pool: asyncpg.Pool, *, adopted: bool = False) -> None:
        self.pool = pool
        self.now = datetime.now(UTC)
        self.suffix = uuid4().hex[:8]
        self.venue = f"{VENUE}-{self.suffix}"
        self.position_id = uuid4()
        self.empty_venue = f"{VENUE}-empty-{self.suffix}"
        self.adopted = adopted

    async def create(self, *, quantity: str = "0.8", stop_price: str = "950") -> None:
        async with self.pool.acquire() as session, session.transaction():
            await session.execute(_VENUE_ROW, self.venue, "Stop Venue")
            await session.execute(_VENUE_ROW, self.empty_venue, "Empty Stop Venue")
            await session.execute(
                _POSITION,
                self.position_id,
                self.venue,
                Decimal(quantity),
                Decimal(stop_price),
                "MONITORED",
                self.adopted,
            )
            if await session.fetchval(_HEAD) is None:
                await session.execute(_STARTUP)

    async def teardown(self) -> None:
        async with self.pool.acquire() as session, session.transaction():
            # Positions first: position.stop_order_id references the order it protects.
            await session.execute("DELETE FROM position WHERE venue_id = $1", self.venue)
            await session.execute('DELETE FROM "order" WHERE venue_id = $1', self.venue)
            await session.execute(
                "DELETE FROM venue WHERE id = ANY($1::text[])",
                [self.venue, self.empty_venue],
            )


@pytest.fixture()
async def seeds(pool: asyncpg.Pool) -> AsyncGenerator[list[Seed], None]:
    created: list[Seed] = []
    try:
        yield created
    finally:
        for seed in created:
            await seed.teardown()


async def _seed(pool: asyncpg.Pool, created: list[Seed], **kwargs: Any) -> Seed:
    seed = Seed(pool, **kwargs)
    await seed.create()
    created.append(seed)
    return seed


def _service(conn: Any, seed: Seed, venue: StopVenue) -> StopAcceptance:
    audit = DbAudit()
    clock = Clock(seed.now)
    return StopAcceptance(
        session=conn,
        writer=OrderWriter(session=conn, venue=venue, audit=audit, clock=clock),  # type: ignore[arg-type]
        audit=audit,
        clock=clock,
    )


def _request(
    seed: Seed,
    *,
    generation: int,
    stop_price: str = "950",
    quantity: str = "0.8",
    position_id: UUID | None = None,
) -> ProtectiveStopRequest:
    return ProtectiveStopRequest(
        request_id=uuid4(),
        position_id=position_id or seed.position_id,
        venue_id=seed.venue,
        pair="BTCIDR",
        quantity=Decimal(quantity),
        stop_price=Decimal(stop_price),
        generation=generation,
        requested_at=seed.now,
        expires_at=seed.now + timedelta(seconds=60),
        signature=uuid4().hex + uuid4().hex,
    )


_LIVE_STOP = """
SELECT id, quantity, limit_price, status
  FROM "order"
 WHERE position_id = $1 AND is_protective AND side = 'SELL'
   AND status IN ('OPEN', 'PARTIALLY_FILLED', 'UNKNOWN')
 LIMIT 1
"""


async def _monitor_cycle(
    pool: asyncpg.Pool, seed: Seed, venue: StopVenue, *, generation: int = 1
) -> list[Accepted | Rejected]:
    """The one job the monitor loop has: every open position with no live stop gets a request."""
    seen: list[Accepted | Rejected] = []
    async with pool.acquire() as conn, conn.transaction():
        service = _service(conn, seed, venue)
        positions = await conn.fetch(
            "SELECT id, quantity, stop_price, stop_generation FROM position"
            " WHERE venue_id = $1 AND status = 'OPEN' ORDER BY id",
            seed.venue,
        )
        for position in positions:
            if await conn.fetchrow(_LIVE_STOP, position["id"]) is not None:
                continue
            request = _request(
                seed,
                generation=generation or int(position["stop_generation"]) + 1,
                stop_price=str(position["stop_price"]),
                quantity=str(position["quantity"]),
                position_id=position["id"],
            )
            seen.append(await service.accept(request, correlation_id="monitor-cycle"))
    return seen


async def _position_row(conn: Any, seed: Seed) -> Mapping[str, Any]:
    row = await conn.fetchrow(
        "SELECT stop_price, stop_protection, stop_order_id, stop_generation, adopted"
        " FROM position WHERE id = $1",
        seed.position_id,
    )
    assert row is not None
    return dict(row)


async def test_a_missing_stop_is_placed_in_one_cycle(pool: asyncpg.Pool, seeds: list[Seed]) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)

    outcomes = await _monitor_cycle(pool, seed, venue)

    async with pool.acquire() as conn:
        position = await _position_row(conn, seed)
        stop = await conn.fetchrow(_LIVE_STOP, seed.position_id)

    assert len(outcomes) == 1 and isinstance(outcomes[0], Accepted)
    assert len(venue.placed) == 1
    placed = venue.placed[0]
    assert placed.side == "SELL"
    assert placed.type == "LIMIT"
    assert placed.limit_price == Decimal("950")
    assert placed.quantity == Decimal("0.8")
    assert stop is not None and stop["id"] == outcomes[0].order_id
    assert position["stop_order_id"] == outcomes[0].order_id
    assert position["stop_protection"] == "NATIVE_ORDER"
    assert position["stop_generation"] == 1


async def test_a_live_stop_is_cancelled_before_the_tighter_one_is_placed(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)

    async with pool.acquire() as conn, conn.transaction():
        previous = await conn.fetchval(
            _LIVE_STOP_ROW,
            seed.venue,
            seed.position_id,
            f"stop-old-{seed.suffix}",
            f"venue-old-{seed.suffix}",
            Decimal("0.8"),
            Decimal("950"),
        )
        await conn.execute(
            "UPDATE position SET stop_order_id = $2, stop_generation = 1 WHERE id = $1",
            seed.position_id,
            previous,
        )
        outcome = await _service(conn, seed, venue).accept(
            _request(seed, generation=2, stop_price="970"), correlation_id="tighten"
        )
        old_status = await conn.fetchval('SELECT status FROM "order" WHERE id = $1', previous)

    assert isinstance(outcome, Accepted)
    assert venue.cancelled == [("BTCIDR", f"venue-old-{seed.suffix}")]
    assert old_status == "CANCELED"
    async with pool.acquire() as conn:
        position = await _position_row(conn, seed)
    assert position["stop_order_id"] == outcome.order_id
    assert position["stop_price"] == Decimal("970")
    assert position["stop_generation"] == 2


async def test_a_wider_stop_is_rejected_and_recorded(pool: asyncpg.Pool, seeds: list[Seed]) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)

    async with pool.acquire() as conn, conn.transaction():
        outcome = await _service(conn, seed, venue).accept(
            _request(seed, generation=1, stop_price="940"), correlation_id="tighten"
        )

    assert isinstance(outcome, Rejected)
    assert outcome.code == "STOP_NOT_TIGHTENING"
    assert venue.placed == []
    async with pool.acquire() as conn:
        position = await _position_row(conn, seed)
        recorded = await conn.fetchval(
            "SELECT payload FROM audit_record WHERE payload->>'refusal' = $1"
            " ORDER BY seq DESC LIMIT 1",
            "STOP_NOT_TIGHTENING",
        )
    assert position["stop_price"] == Decimal("950")
    assert position["stop_order_id"] is None
    payload = json.loads(recorded) if isinstance(recorded, str) else recorded
    assert payload is not None and payload["status"] == "REFUSED"


async def test_a_stale_generation_is_ignored_without_touching_the_venue(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE position SET stop_generation = 3, stop_price = 970 WHERE id = $1",
            seed.position_id,
        )
    async with pool.acquire() as conn, conn.transaction():
        outcome = await _service(conn, seed, venue).accept(
            _request(seed, generation=2, stop_price="975"), correlation_id="stale"
        )

    assert isinstance(outcome, Rejected)
    assert outcome.code == "STALE_GENERATION"
    assert venue.placed == [] and venue.cancelled == []
    async with pool.acquire() as conn:
        position = await _position_row(conn, seed)
        stops = await conn.fetchval(
            'SELECT count(*) FROM "order" WHERE position_id = $1', seed.position_id
        )
    assert position["stop_price"] == Decimal("970")
    assert stops == 0


async def test_a_stop_larger_than_the_position_is_rejected(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)

    async with pool.acquire() as conn, conn.transaction():
        outcome = await _service(conn, seed, venue).accept(
            _request(seed, generation=1, quantity="1.5"), correlation_id="over-size"
        )

    assert isinstance(outcome, Rejected)
    assert outcome.code == "QUANTITY_EXCEEDS_POSITION"
    assert venue.placed == []


async def test_no_orders_are_emitted_when_there_are_no_positions(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    venue = StopVenue(venue_id=seed.venue)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM position WHERE id = $1", seed.position_id)

    outcomes = await _monitor_cycle(pool, seed, venue)

    assert outcomes == []
    assert venue.placed == [] and venue.cancelled == []


async def test_an_adopted_position_without_a_stop_forces_halted_when_placement_fails(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds, adopted=True)
    venue = StopVenue(venue_id=seed.venue)
    venue.fail_with = "unavailable"

    async with pool.acquire() as conn, conn.transaction():
        outcome = await _service(conn, seed, venue).accept(
            _request(seed, generation=1), correlation_id="adopted"
        )

    assert isinstance(outcome, Rejected)
    assert outcome.code == "PROTECTIVE_STOP_NOT_PLACED"
    async with pool.acquire() as conn:
        mode = await conn.fetchval(_HEAD)
        incident = await conn.fetchrow(
            "SELECT class, kind, severity, auto_action FROM incident"
            " WHERE kind = 'ADOPTED_POSITION_UNPROTECTED' ORDER BY opened_at DESC LIMIT 1"
        )
    assert mode == "HALTED"
    assert incident is not None
    assert (incident["class"], incident["severity"], incident["auto_action"]) == (
        "SAFETY",
        "SEV1",
        "HALTED",
    )
