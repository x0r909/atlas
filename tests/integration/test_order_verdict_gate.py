"""T064/T065 (database half): a verdict is spent once, and the order is recorded with it."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg
import pytest
from atlas_execution.gate import Refused, _Connection, consume, order_fields
from atlas_schemas.order import SignedOrder
from atlas_schemas.verdict_sign import sign_order_with_verdict

KEY = b"integration-signing-key"
DSN = os.environ.get("ATLAS_DB_DSN")


@pytest.fixture()
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    if not DSN:
        pytest.skip("ATLAS_DB_DSN not set")
    created = await asyncpg.create_pool(DSN)
    async with created.acquire() as connection:
        if await connection.fetchval("SELECT to_regclass('public.risk_verdict')") is None:
            await created.close()
            pytest.skip("risk_verdict does not exist; run `alembic upgrade head` first")
    try:
        yield created
    finally:
        await created.close()


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
    'indodax', 1, 0.00000, 0.30000, NULL, 0.10000,
    0.21000, 0.10000, 11.00000, 'SELL_ONLY', 10000,
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


async def _seed(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    cycle_id, proposal_id, verdict_id = uuid4(), uuid4(), uuid4()
    snapshot_id = uuid4()
    now = datetime.now(UTC)
    async with pool.acquire() as session, session.transaction():
        await session.execute(_CYCLE, cycle_id)
        await session.execute(_PROPOSAL, proposal_id, cycle_id)
        profile_id = await session.fetchval(_PROFILE)
        await session.execute(_SNAPSHOT, snapshot_id)
        await session.execute(
            _VERDICT,
            verdict_id,
            proposal_id,
            profile_id,
            snapshot_id,
            "a" * 64,
            "b" * 64,
            now - timedelta(seconds=5),
            now + timedelta(seconds=55),
        )
    return proposal_id, verdict_id


def _order(proposal_id: UUID, verdict_id: UUID) -> SignedOrder:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "signed_order_id": uuid4(),
        "verdict_id": verdict_id,
        "proposal_id": proposal_id,
        "venue_id": "indodax",
        "pair": "BTCIDR",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": Decimal("100"),
        "limit_price": Decimal("1000"),
        "stop_price": Decimal("950"),
        "client_order_id": f"atlas-{uuid4().hex[:8]}-{uuid4().hex[:8]}-1",
        "signed_at": now,
        "expires_at": now + timedelta(seconds=30),
    }
    unsigned = SignedOrder(**{**values, "signature": "0" * 64})
    signature = sign_order_with_verdict(order_fields(unsigned), str(verdict_id), KEY)
    return SignedOrder(**{**values, "signature": signature})


async def test_a_verdict_can_be_spent_exactly_once(pool: asyncpg.Pool) -> None:
    proposal_id, verdict_id = await _seed(pool)
    first, second = _order(proposal_id, verdict_id), _order(proposal_id, verdict_id)

    async with pool.acquire() as conn, conn.transaction():
        order_row_id = await consume(cast(_Connection, conn), first)
        assert order_row_id != first.signed_order_id

    async with pool.acquire() as conn:
        with pytest.raises(Refused) as caught:
            async with conn.transaction():
                await consume(cast(_Connection, conn), second)
    assert caught.value.code == "VERDICT_CONSUMED"

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM signed_order WHERE verdict_id = $1", verdict_id)
        spent = await conn.fetchrow(
            "SELECT consumed_at, consumed_by_order_id FROM risk_verdict WHERE id = $1", verdict_id
        )
    assert len(rows) == 1
    assert rows[0]["id"] == first.signed_order_id
    assert spent is not None and spent["consumed_at"] is not None
    assert spent["consumed_by_order_id"] == order_row_id


async def test_a_halted_system_refuses_the_entry_inside_the_same_transaction(
    pool: asyncpg.Pool,
) -> None:
    proposal_id, verdict_id = await _seed(pool)
    order = _order(proposal_id, verdict_id)
    async with pool.acquire() as session:
        await session.execute("ALTER TABLE mode_state DISABLE TRIGGER USER")
        await session.execute(
            "INSERT INTO mode_state (mode, reason_code, actor)"
            " VALUES ('HALTED', 'KILL_SWITCH', 'OPERATOR')"
        )
        await session.execute("ALTER TABLE mode_state ENABLE TRIGGER USER")
    try:
        async with pool.acquire() as session:
            with pytest.raises(Refused) as caught:
                async with session.transaction():
                    await consume(cast(_Connection, session), order)
        assert caught.value.code == "MODE_NOT_TRADABLE"
        async with pool.acquire() as session:
            assert (
                await session.fetchval(
                    "SELECT consumed_at FROM risk_verdict WHERE id = $1", verdict_id
                )
                is None
            )
    finally:
        async with pool.acquire() as session:
            await session.execute("ALTER TABLE mode_state DISABLE TRIGGER USER")
            await session.execute("DELETE FROM mode_state WHERE mode = 'HALTED'")
            await session.execute("ALTER TABLE mode_state ENABLE TRIGGER USER")


async def test_an_unknown_verdict_is_refused_before_any_insert(pool: asyncpg.Pool) -> None:
    proposal_id, _ = await _seed(pool)
    order = _order(proposal_id, uuid4())
    async with pool.acquire() as conn:
        with pytest.raises(Refused) as caught:
            async with conn.transaction():
                await consume(cast(_Connection, conn), order)
    assert caught.value.code == "VERDICT_UNKNOWN_ID"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM signed_order WHERE id = $1", order.signed_order_id
            )
            == 0
        )
