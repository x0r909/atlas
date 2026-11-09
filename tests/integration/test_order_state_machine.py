"""T068: the order state machine advances only along the audited graph.

The graph is `data-model.md` §5. Two properties matter here:

* a transition the graph does not allow is refused *and* leaves a record, because an operator
  reconstructing an incident needs to see the attempt, not only the successful moves;
* every accepted transition writes exactly one audit record, so the trail and the state can
  never disagree about how many moves happened.

`UNKNOWN` is non-terminal and freezes its venue: at most one order may sit in it per venue,
which is what stops a retry from becoming a second live order (FR-069).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg
import pytest
from atlas_common import audit_chain
from atlas_execution.state_machine import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransition,
    OrderState,
    OrderStateMachine,
    VenueFrozen,
)

DSN = os.environ.get("ATLAS_DB_DSN")


class DbAudit:
    """The real audit writer over the asyncpg session the machine was given."""

    async def append(
        self, session: Any, record_type: str, payload: Mapping[str, Any]
    ) -> tuple[int, str]:
        return await audit_chain.append(session, record_type, payload)


@pytest.fixture()
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    if not DSN:
        pytest.skip("ATLAS_DB_DSN not set")
    created = await asyncpg.create_pool(DSN)
    async with created.acquire() as connection:
        for table in ("order", "signed_order", "audit_record"):
            if await connection.fetchval("SELECT to_regclass($1)", f"public.{table}") is None:
                await created.close()
                pytest.skip(f"{table} does not exist; run `alembic upgrade head` first")
    try:
        yield created
    finally:
        await created.close()


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def utc(self) -> datetime:
        return self._at


_VENUE = """
INSERT INTO venue (id, display_name, regulator, enabled_for_live, api_base)
VALUES ($1, $2, 'NONE', false, 'https://example.invalid')
ON CONFLICT (id) DO NOTHING
"""

_CYCLE = """
INSERT INTO decision_cycle (id, mode, trigger, started_at, finished_at, outcome)
VALUES ($1, 'PAPER', 'SCHEDULE', now(), now(), 'SKIPPED')
"""

_PROPOSAL = """
INSERT INTO trade_proposal (
    id, cycle_id, pair, entry_type, entry_price, stop_price, target_price,
    horizon_hours, intended_notional_idr, rationale_text, confidence
) VALUES ($1, $2, 'BTCIDR', 'LIMIT', 1000, 950, 1200, 24, 100000, 'integration fixture', 0.60)
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
ON CONFLICT (venue_id, version) DO UPDATE SET verified_at = now()
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

_SIGNED_ORDER = """
INSERT INTO signed_order (
    id, verdict_id, proposal_id, venue_id, pair, side, type, quantity,
    limit_price, stop_price, client_order_id, signature, signed_at, expires_at
) VALUES ($1, $2, $3, $4, 'BTCIDR', 'BUY', 'LIMIT', $5, 1000, 950, $6, $7, $8, $9)
"""

_ORDER = """
INSERT INTO "order" (
    id, venue_id, client_order_id, signed_order_id, pair, side, type, quantity,
    limit_price, status, filled_quantity, unknown_since
) VALUES ($1, $2, $3, $4, 'BTCIDR', 'BUY', 'LIMIT', $5, 1000, $6, $7, $8)
"""


class Seed:
    """A venue with its profile and snapshot, then one proposal+order chain per order.

    `risk_verdict` allows one verdict per proposal, so every order carries its own
    cycle/proposal/verdict chain. The venue id is run-unique: a leftover `UNKNOWN` row from
    an earlier run must not freeze this run's venue, which is the property under test.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self.now = datetime.now(UTC)
        self.snapshot_id = uuid4()
        self.suffix = uuid4().hex[:8]
        self.venues: dict[str, str] = {}
        self.profiles: dict[str, int] = {}
        self.cycle_ids: list[UUID] = []
        self.proposal_ids: list[UUID] = []
        self.verdict_ids: list[UUID] = []

    async def base(self, venue_id: str) -> str:
        unique = f"{venue_id}-{self.suffix}"
        async with self.pool.acquire() as session, session.transaction():
            await session.execute(_VENUE, unique, venue_id.title())
            await session.execute(_SNAPSHOT, self.snapshot_id)
            profile_id = await session.fetchval(_PROFILE, unique)
        assert profile_id is not None
        self.venues[venue_id] = unique
        self.profiles[venue_id] = int(profile_id)
        return unique

    async def order(
        self,
        venue_id: str,
        *,
        status: str = "PENDING_NEW",
        quantity: int = 100,
        filled_quantity: int = 0,
    ) -> UUID:
        unique = self.venues[venue_id]
        order_id, signed_id, verdict_id = uuid4(), uuid4(), uuid4()
        cycle_id, proposal_id = uuid4(), uuid4()
        client_order_id = f"atlas-{uuid4().hex[:8]}-{uuid4().hex[:8]}-1"
        unknown_since = self.now if status == "UNKNOWN" else None
        async with self.pool.acquire() as session, session.transaction():
            await session.execute(_CYCLE, cycle_id)
            await session.execute(_PROPOSAL, proposal_id, cycle_id)
            await session.execute(
                _VERDICT,
                verdict_id,
                proposal_id,
                self.profiles[venue_id],
                self.snapshot_id,
                "a" * 64,
                "b" * 64,
                self.now - timedelta(seconds=5),
                self.now + timedelta(seconds=55),
            )
            await session.execute(
                _SIGNED_ORDER,
                signed_id,
                verdict_id,
                proposal_id,
                unique,
                quantity,
                client_order_id,
                "c" * 64,
                self.now,
                self.now + timedelta(seconds=30),
            )
            await session.execute(
                _ORDER,
                order_id,
                unique,
                client_order_id,
                signed_id,
                quantity,
                status,
                filled_quantity,
                unknown_since,
            )
        self.cycle_ids.append(cycle_id)
        self.proposal_ids.append(proposal_id)
        self.verdict_ids.append(verdict_id)
        return order_id

    async def teardown(self) -> None:
        async with self.pool.acquire() as session, session.transaction():
            for unique in self.venues.values():
                await session.execute('DELETE FROM "order" WHERE venue_id = $1', unique)
                await session.execute("DELETE FROM signed_order WHERE venue_id = $1", unique)
            if self.verdict_ids:
                await session.execute(
                    "DELETE FROM risk_verdict WHERE id = ANY($1::uuid[])", self.verdict_ids
                )
            if self.proposal_ids:
                await session.execute(
                    "DELETE FROM trade_proposal WHERE id = ANY($1::uuid[])", self.proposal_ids
                )
            if self.cycle_ids:
                await session.execute(
                    "DELETE FROM decision_cycle WHERE id = ANY($1::uuid[])", self.cycle_ids
                )
            for unique in self.venues.values():
                await session.execute("DELETE FROM venue_profile WHERE venue_id = $1", unique)
                await session.execute("DELETE FROM venue WHERE id = $1", unique)
            await session.execute("DELETE FROM portfolio_snapshot WHERE id = $1", self.snapshot_id)


def _machine(clock: datetime) -> OrderStateMachine:
    return OrderStateMachine(audit=DbAudit(), clock=FixedClock(clock))


@pytest.fixture()
async def seeds(pool: asyncpg.Pool) -> AsyncGenerator[Callable[[], Seed], None]:
    """A Seed per call, torn down afterwards so a rerun starts from the same state."""
    created: list[Seed] = []

    def make() -> Seed:
        seed = Seed(pool)
        created.append(seed)
        return seed

    try:
        yield make
    finally:
        for seed in created:
            await seed.teardown()


async def _audit_rows(pool: asyncpg.Pool, order_id: UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as session:
        rows = await session.fetch(
            "SELECT payload FROM audit_record"
            " WHERE record_type = 'ORDER' AND payload->>'order_id' = $1"
            " ORDER BY seq",
            str(order_id),
        )
    return [
        json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
        for row in rows
    ]


async def test_legal_transitions_advance_and_each_is_audited_exactly_once(
    pool: asyncpg.Pool, seeds: Callable[[], Seed]
) -> None:
    seed = seeds()
    await seed.base("indodax")
    order_id = await seed.order("indodax")
    machine = _machine(seed.now)

    moves = [
        (OrderState.PENDING_NEW, OrderState.OPEN, 0),
        (OrderState.OPEN, OrderState.PARTIALLY_FILLED, 40),
        (OrderState.PARTIALLY_FILLED, OrderState.FILLED, 100),
    ]
    async with pool.acquire() as session:
        for index, (source, target, filled) in enumerate(moves):
            async with session.transaction():
                moved = await machine.transition(
                    cast("Any", session),
                    order_id,
                    target,
                    correlation_id=f"legal-{index}",
                    filled_quantity=Decimal(filled),
                )
            assert (moved.from_state, moved.to_state) == (source, target)

        status = await session.fetchval('SELECT status FROM "order" WHERE id = $1', order_id)
    records = await _audit_rows(pool, order_id)

    assert status == "FILLED"
    assert len(records) == len(moves)
    assert [record["transition"] for record in records] == [
        "PENDING_NEW->OPEN",
        "OPEN->PARTIALLY_FILLED",
        "PARTIALLY_FILLED->FILLED",
    ]
    assert len({record["correlation_id"] for record in records}) == len(moves)


async def test_filled_cannot_become_canceled_and_the_refusal_is_logged(
    pool: asyncpg.Pool, seeds: Callable[[], Seed]
) -> None:
    seed = seeds()
    await seed.base("indodax")
    order_id = await seed.order("indodax", status="FILLED", filled_quantity=100)
    machine = _machine(seed.now)

    async with pool.acquire() as session:
        with pytest.raises(IllegalTransition) as caught:
            await machine.transition(
                cast("Any", session),
                order_id,
                OrderState.CANCELED,
                correlation_id="illegal-1",
            )

    assert caught.value.code == "ILLEGAL_TRANSITION"
    async with pool.acquire() as session:
        status = await session.fetchval('SELECT status FROM "order" WHERE id = $1', order_id)
    assert status == "FILLED"

    records = await _audit_rows(pool, order_id)
    assert len(records) == 1
    assert records[0]["status"] == "REFUSED"
    assert records[0]["transition"] == "FILLED->CANCELED"
    assert records[0]["correlation_id"] == "illegal-1"


async def test_unknown_is_non_terminal_and_only_one_may_exist_per_venue(
    pool: asyncpg.Pool, seeds: Callable[[], Seed]
) -> None:
    indodax, other = seeds(), seeds()
    await indodax.base("indodax")
    await other.base("tokocrypto")
    first = await indodax.order("indodax")
    blocked = await indodax.order("indodax")
    elsewhere = await other.order("tokocrypto")
    machine = _machine(indodax.now)

    assert OrderState.UNKNOWN not in TERMINAL_STATES
    assert LEGAL_TRANSITIONS[OrderState.UNKNOWN] == frozenset(
        {OrderState.OPEN, OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED}
    )

    async with pool.acquire() as session:
        async with session.transaction():
            moved = await machine.transition(
                cast("Any", session), first, OrderState.UNKNOWN, correlation_id="unknown-1"
            )
        assert moved.to_state is OrderState.UNKNOWN
        unknown_since = await session.fetchval(
            'SELECT unknown_since FROM "order" WHERE id = $1', first
        )
        assert unknown_since is not None

        with pytest.raises(VenueFrozen) as caught:
            await machine.transition(
                cast("Any", session), blocked, OrderState.UNKNOWN, correlation_id="unknown-2"
            )
        assert caught.value.code == "VENUE_FROZEN"
        status = await session.fetchval('SELECT status FROM "order" WHERE id = $1', blocked)
        assert status == "PENDING_NEW"

        async with session.transaction():
            other_move = await machine.transition(
                cast("Any", session), elsewhere, OrderState.UNKNOWN, correlation_id="unknown-3"
            )
        assert other_move.to_state is OrderState.UNKNOWN

        async with session.transaction():
            await machine.transition(
                cast("Any", session), first, OrderState.OPEN, correlation_id="resolve"
            )
        async with session.transaction():
            await machine.transition(
                cast("Any", session), blocked, OrderState.UNKNOWN, correlation_id="unknown-4"
            )
        status = await session.fetchval('SELECT status FROM "order" WHERE id = $1', blocked)
        resolved = await session.fetchval('SELECT unknown_since FROM "order" WHERE id = $1', first)
        assert status == "UNKNOWN"
        assert resolved is None
