"""T070: a partial fill never leaves the position unprotected (FR-068, SC-023).

Three properties are asserted against the real database, because all three are constraints a
mock would happily violate:

* the stop is sized to the *filled* quantity, not the order's quantity, and is re-sized on
  every later fill of the same order;
* the unfilled remainder is cancelled after ``partial_fill_timeout_s`` (45 s), the filled
  quantity survives the cancel, and the cancel happens exactly once;
* every committed step leaves an open position whose stop exists and matches its quantity.

The venue is a fake that only implements ``cancel_order``: fills arrive as execution reports,
so nothing here needs to place an order on an exchange and no credential is involved.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg
import pytest
from atlas_common import audit_chain
from atlas_execution.fills import Fill, FillHandler
from atlas_execution.orders import OrderWriter
from atlas_execution.position_sync import PositionSync
from atlas_execution.state_machine import OrderStateMachine
from atlas_schemas.venue import VenueOrderAck
from atlas_schemas.venue_port import VenuePort

DSN = os.environ.get("ATLAS_DB_DSN")
VENUE = "indodax"


class DbAudit:
    async def append(
        self, session: Any, record_type: str, payload: Mapping[str, Any]
    ) -> tuple[int, str]:
        return await audit_chain.append(session, record_type, payload)


class MoveableClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def utc(self) -> datetime:
        return self.at


class FakeVenue:
    """Records cancels; it has no other order surface the code under test could reach."""

    def __init__(self) -> None:
        self.cancels: list[tuple[str, str]] = []

    def profile(self) -> Any:
        raise AssertionError("the fill path must not read the venue profile over the port")

    async def cancel_order(self, pair: str, venue_order_id: str) -> VenueOrderAck:
        self.cancels.append((pair, venue_order_id))
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
        for table in ("order", "fill", "position", "signed_order"):
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

_CYCLE = """
INSERT INTO decision_cycle (id, mode, trigger, started_at, finished_at, outcome)
VALUES ($1, 'PAPER', 'SCHEDULE', now(), now(), 'SKIPPED')
"""

_PROPOSAL = """
INSERT INTO trade_proposal (
    id, cycle_id, pair, entry_type, entry_price, stop_price, target_price,
    horizon_hours, intended_notional_idr, rationale_text, confidence
) VALUES ($1, $2, 'BTCIDR', 'LIMIT', 1000, 950, 1200, 24, 100000, 'fill fixture', 0.60)
"""

_PROFILE = """
INSERT INTO venue_profile (
    venue_id, version, fee_maker_pct, fee_taker_pct, cfx_fee_pct, cfx_fee_pct_assumed,
    tax_buy_pct_assumed, tax_pph_pct, tax_ppn_pct, tax_applies_to, min_notional_idr,
    min_order_step, tick_size, rate_limit, max_leverage, native_stop_orders,
    supports_order_modify, cost_model_version, verified_at
) VALUES (
    $1, 1, 0.10000, 0.20000, NULL, 0.10000,
    0.21000, 0.21000, 0.00000, 'SELL_ONLY', 10000,
    '{"BTCIDR": "0.00000001"}'::jsonb, '{"BTCIDR": "1"}'::jsonb, '{}'::jsonb, 1, false,
    false, '1', now()
)
RETURNING id
"""

_SNAPSHOT = """
INSERT INTO portfolio_snapshot (
    id, equity_idr, cash_idr, positions_value_idr, unrealised_pnl_idr,
    realised_pnl_day_idr, drawdown_pct, peak_equity_idr
) VALUES ($1, 1000000, 1000000, 0, 0, 0, 0, 1000000)
"""

_VERDICT = """
INSERT INTO risk_verdict (
    id, proposal_id, approved, rule_results, sized_quantity, sized_notional_idr,
    risk_config_version, venue_profile_id, portfolio_snapshot_id, input_hash,
    signature, verdict_at, expires_at
) VALUES ($1, $2, true, '[]'::jsonb, 100, 100000, 1, $3, $4, $5, $6, $7, $8)
"""

_RISK_CONFIG = """
INSERT INTO risk_config (version) VALUES (1)
ON CONFLICT (version) DO NOTHING
"""

_SIGNED_ORDER = """
INSERT INTO signed_order (
    id, verdict_id, proposal_id, venue_id, pair, side, type, quantity,
    limit_price, stop_price, client_order_id, signature, signed_at, expires_at
) VALUES ($1, $2, $3, $4, 'BTCIDR', 'BUY', 'LIMIT', $5, 1000, 950, $6, $7, $8, $9)
"""

_ORDER = """
INSERT INTO "order" (
    id, venue_id, venue_order_id, client_order_id, signed_order_id, pair, side, type,
    quantity, limit_price, status, filled_quantity, submitted_at
) VALUES ($1, $2, $3, $4, $5, 'BTCIDR', 'BUY', 'LIMIT', $6, 1000, 'OPEN', 0, $7)
"""


class Seed:
    """One venue, one open 100 BTCIDR entry order, and everything the fill path joins to."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self.now = datetime.now(UTC)
        self.suffix = uuid4().hex[:8]
        self.venue = f"{VENUE}-{self.suffix}"
        self.snapshot_id = uuid4()
        self.cycle_ids: list[UUID] = []
        self.proposal_ids: list[UUID] = []
        self.verdict_ids: list[UUID] = []
        self.signed_order_ids: list[UUID] = []
        self.order_id = uuid4()
        self.client_order_id = f"atlas-{uuid4().hex[:8]}-{uuid4().hex[:8]}-1"
        self.venue_order_id = f"venue-{uuid4().hex[:8]}"

    async def create(self) -> None:
        proposal_id, verdict_id, signed_id, cycle_id = uuid4(), uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as session, session.transaction():
            await session.execute(_VENUE_ROW, self.venue, "Fill Venue")
            await session.execute(_SNAPSHOT, self.snapshot_id)
            profile_id = await session.fetchval(_PROFILE, self.venue)
            await session.execute(_RISK_CONFIG)
            await session.execute(_CYCLE, cycle_id)
            await session.execute(_PROPOSAL, proposal_id, cycle_id)
            await session.execute(
                _VERDICT,
                verdict_id,
                proposal_id,
                profile_id,
                self.snapshot_id,
                "a" * 64,
                "b" * 64,
                # A verdict lives 60 s at most (risk_verdict_expiry_window); the 45 s
                # remainder timeout must still land inside its life.
                self.now - timedelta(seconds=5),
                self.now + timedelta(seconds=50),
            )
            await session.execute(
                _SIGNED_ORDER,
                signed_id,
                verdict_id,
                proposal_id,
                self.venue,
                Decimal("100"),
                self.client_order_id,
                "c" * 64,
                self.now - timedelta(seconds=5),
                self.now + timedelta(seconds=50),
            )
            await session.execute(
                _ORDER,
                self.order_id,
                self.venue,
                self.venue_order_id,
                self.client_order_id,
                signed_id,
                Decimal("100"),
                self.now,
            )
        self.cycle_ids.append(cycle_id)
        self.proposal_ids.append(proposal_id)
        self.verdict_ids.append(verdict_id)
        self.signed_order_ids.append(signed_id)

    async def teardown(self) -> None:
        async with self.pool.acquire() as session, session.transaction():
            await session.execute("DELETE FROM position WHERE venue_id = $1", self.venue)
            await session.execute("DELETE FROM fill WHERE venue_id = $1", self.venue)
            await session.execute('DELETE FROM "order" WHERE venue_id = $1', self.venue)
            await session.execute("DELETE FROM signed_order WHERE venue_id = $1", self.venue)
            await session.execute(
                "DELETE FROM risk_verdict WHERE id = ANY($1::uuid[])", self.verdict_ids
            )
            await session.execute(
                "DELETE FROM trade_proposal WHERE id = ANY($1::uuid[])", self.proposal_ids
            )
            await session.execute(
                "DELETE FROM decision_cycle WHERE id = ANY($1::uuid[])", self.cycle_ids
            )
            await session.execute("DELETE FROM venue_profile WHERE venue_id = $1", self.venue)
            await session.execute("DELETE FROM venue WHERE id = $1", self.venue)
            await session.execute("DELETE FROM portfolio_snapshot WHERE id = $1", self.snapshot_id)


@pytest.fixture()
async def seeds(pool: asyncpg.Pool) -> AsyncGenerator[list[Seed], None]:
    created: list[Seed] = []
    try:
        yield created
    finally:
        for seed in created:
            await seed.teardown()


async def _seed(pool: asyncpg.Pool, created: list[Seed]) -> Seed:
    seed = Seed(pool)
    await seed.create()
    created.append(seed)
    return seed


def _handler(conn: Any, seed: Seed, clock: MoveableClock, venue: FakeVenue) -> FillHandler:
    audit = DbAudit()
    return FillHandler(
        session=conn,
        machine=OrderStateMachine(audit=audit, clock=clock),
        writer=OrderWriter(session=conn, venue=cast(VenuePort, venue), audit=audit, clock=clock),
        positions=PositionSync(session=conn, clock=clock),
        clock=clock,
    )


def _fill(seed: Seed, quantity: str, price: str = "1000", trade: str | None = None) -> Fill:
    return Fill(
        order_id=seed.order_id,
        venue_trade_id=trade or f"trade-{uuid4().hex[:8]}",
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_at=seed.now,
    )


async def _order_state(session: Any, order_id: UUID) -> Mapping[str, Any]:
    row = await session.fetchrow(
        'SELECT status, filled_quantity, avg_fill_price, remainder_action FROM "order"'
        " WHERE id = $1",
        order_id,
    )
    assert row is not None
    return row


async def _position(session: Any, venue: str) -> Mapping[str, Any] | None:
    return await session.fetchrow(
        "SELECT quantity, avg_entry_price, stop_price, stop_protection, target_price,"
        " status, time_stop_at FROM position WHERE venue_id = $1 AND status = 'OPEN'",
        venue,
    )


async def test_a_partial_fill_sizes_the_stop_to_the_filled_quantity(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()

    async with pool.acquire() as conn, conn.transaction():
        handler = _handler(conn, seed, clock, venue)
        outcome = await handler.record(_fill(seed, "40"), correlation_id="fill-1")

        order = await _order_state(conn, seed.order_id)
        position = await _position(conn, seed.venue)

    assert order["status"] == "PARTIALLY_FILLED"
    assert order["filled_quantity"] == Decimal("40")
    assert order["remainder_action"] == "CANCEL_PENDING"
    assert outcome.stop is not None
    assert outcome.stop.quantity == Decimal("40")
    assert position is not None
    assert position["quantity"] == Decimal("40")
    assert position["stop_price"] == Decimal("950")
    assert position["stop_protection"] == "MONITORED"
    assert position["target_price"] == Decimal("1200")
    assert position["time_stop_at"] == seed.now + timedelta(hours=24)
    assert venue.cancels == []


async def test_a_second_partial_fill_resizes_the_same_stop(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()

    async with pool.acquire() as conn, conn.transaction():
        handler = _handler(conn, seed, clock, venue)
        first = await handler.record(_fill(seed, "40"), correlation_id="fill-1")
        second = await handler.record(_fill(seed, "40"), correlation_id="fill-2")
        position = await _position(conn, seed.venue)

    assert first.stop is not None and second.stop is not None
    assert second.stop.position_id == first.stop.position_id
    assert second.stop.quantity == Decimal("80")
    assert second.stop.generation > first.stop.generation
    assert second.stop.stop_price == first.stop.stop_price == Decimal("950")
    assert position is not None and position["quantity"] == Decimal("80")


async def test_the_completing_fill_closes_the_order_at_the_full_quantity(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()

    async with pool.acquire() as conn, conn.transaction():
        handler = _handler(conn, seed, clock, venue)
        await handler.record(_fill(seed, "40"), correlation_id="fill-1")
        outcome = await handler.record(_fill(seed, "60"), correlation_id="fill-2")
        order = await _order_state(conn, seed.order_id)
        position = await _position(conn, seed.venue)

    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == Decimal("100")
    assert order["remainder_action"] == "NONE"
    assert outcome.remainder_due_at is None
    assert position is not None and position["quantity"] == Decimal("100")
    assert outcome.stop is not None and outcome.stop.quantity == Decimal("100")


async def test_a_duplicate_report_does_not_fill_twice(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()
    fill = _fill(seed, "40")

    async with pool.acquire() as conn, conn.transaction():
        handler = _handler(conn, seed, clock, venue)
        await handler.record(fill, correlation_id="fill-1")
        again = await handler.record(fill, correlation_id="fill-1")
        order = await _order_state(conn, seed.order_id)
        fills = await conn.fetchval("SELECT count(*) FROM fill WHERE order_id = $1", seed.order_id)

    assert fills == 1
    assert order["filled_quantity"] == Decimal("40")
    assert again.stop is None


async def test_the_remainder_is_cancelled_once_after_the_timeout(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()

    async with pool.acquire() as conn, conn.transaction():
        handler = _handler(conn, seed, clock, venue)
        outcome = await handler.record(_fill(seed, "40"), correlation_id="fill-1")
    assert outcome.remainder_due_at == seed.now + timedelta(seconds=45)

    clock.at = seed.now + timedelta(seconds=44)
    async with pool.acquire() as conn:
        assert (
            await _handler(conn, seed, clock, venue).expire_remainders(correlation_id="sweep") == []
        )
    assert venue.cancels == []

    clock.at = seed.now + timedelta(seconds=45)
    async with pool.acquire() as conn, conn.transaction():
        cancelled = await _handler(conn, seed, clock, venue).expire_remainders(
            correlation_id="sweep"
        )
        order = await _order_state(conn, seed.order_id)
        position = await _position(conn, seed.venue)

    assert [c.order_id for c in cancelled] == [seed.order_id]
    assert venue.cancels == [("BTCIDR", seed.venue_order_id)]
    assert order["status"] == "CANCELED"
    assert order["filled_quantity"] == Decimal("40")
    assert order["remainder_action"] == "CANCELED"
    assert position is not None and position["quantity"] == Decimal("40")

    async with pool.acquire() as conn, conn.transaction():
        assert (
            await _handler(conn, seed, clock, venue).expire_remainders(correlation_id="sweep-again")
            == []
        )
    assert venue.cancels == [("BTCIDR", seed.venue_order_id)]


async def test_no_committed_step_leaves_an_open_position_without_a_matching_stop(
    pool: asyncpg.Pool, seeds: list[Seed]
) -> None:
    seed = await _seed(pool, seeds)
    clock, venue = MoveableClock(seed.now), FakeVenue()

    for index in range(1, 6):
        async with pool.acquire() as conn, conn.transaction():
            handler = _handler(conn, seed, clock, venue)
            await handler.record(_fill(seed, "10"), correlation_id=f"fill-{index}")
            unprotected = await conn.fetchrow(
                "SELECT count(*) AS total FROM position"
                " WHERE venue_id = $1 AND status = 'OPEN'"
                " AND (stop_price IS NULL OR stop_price <= 0 OR quantity <= 0)",
                seed.venue,
            )
            mismatched = await conn.fetchrow(
                "SELECT count(*) AS total FROM position p"
                ' JOIN "order" o ON o.position_id = p.id'
                " WHERE p.venue_id = $1 AND p.status = 'OPEN' AND p.quantity <> o.filled_quantity",
                seed.venue,
            )
        assert unprotected is not None and unprotected["total"] == 0
        assert mismatched is not None and mismatched["total"] == 0
