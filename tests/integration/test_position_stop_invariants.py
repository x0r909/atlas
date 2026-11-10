"""Q1 after migration 0009: what keeps a long's stop sane once the entry-side CHECK is gone.

Dropping `position_stop_below_entry` removed the only constraint that tied a stop to a price.
Three properties replaced it, and all three need to hold in the database rather than in a caller
that might forget:

* the stop of a long never falls (a wider stop is a refusal, not an overwrite);
* `stop_generation` only moves forward, so a stale monitor message cannot win a race;
* the stop never sits at or above the last known market price (it cannot protect a position it
  would already have closed).

The trigger is the backstop; `execute.accept` refuses the same moves first. The tests below
speak to Postgres directly, which is the only way to prove the backstop exists.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

DSN = os.environ.get("ATLAS_DB_DSN")
VENUE = "indodax"


@pytest.fixture()
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    if not DSN:
        pytest.skip("ATLAS_DB_DSN not set")
    created = await asyncpg.create_pool(DSN)
    async with created.acquire() as connection:
        for table in ("position", "venue", "quote_snapshot"):
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

_QUOTE = """
INSERT INTO quote_snapshot (venue_id, pair, bid, ask, last, bid_size, ask_size, venue_ts)
VALUES ($1, 'BTCIDR', $2, $3, $3, 1, 1, now())
"""

_POSITION = """
INSERT INTO position (
    id, venue_id, pair, quantity, avg_entry_price, notional_idr, stop_price,
    stop_protection, stop_generation, target_price, time_stop_at, adopted
) VALUES ($1, $2, 'BTCIDR', 1, 1000, 1000, $3, 'MONITORED', 1, 1200,
          now() + interval '24 hours', false)
"""


@pytest.fixture()
async def seeded(pool: asyncpg.Pool) -> AsyncGenerator[dict[str, object], None]:
    venue = f"{VENUE}-{uuid4().hex[:8]}"
    position_id = uuid4()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(_VENUE_ROW, venue, "Invariant Venue")  # nosemgrep: parameterized
        await conn.execute(_QUOTE, venue, Decimal("990"), Decimal("1010"))
        await conn.execute(_POSITION, position_id, venue, Decimal("950"))
    try:
        yield {"venue": venue, "position_id": position_id}
    finally:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM position WHERE venue_id = $1", venue)
            await conn.execute("DELETE FROM quote_snapshot WHERE venue_id = $1", venue)
            await conn.execute("DELETE FROM venue WHERE id = $1", venue)


async def _stop(conn: Any, position_id: object) -> Decimal:
    value = await conn.fetchval("SELECT stop_price FROM position WHERE id = $1", position_id)
    assert value is not None
    return Decimal(value)


async def test_a_long_stop_never_falls(pool: asyncpg.Pool, seeded: dict[str, object]) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError, match="stop_price"):
            await conn.execute(
                "UPDATE position SET stop_price = 940 WHERE id = $1", seeded["position_id"]
            )
        assert await _stop(conn, seeded["position_id"]) == Decimal("950")


async def test_stop_generation_only_moves_forward(
    pool: asyncpg.Pool, seeded: dict[str, object]
) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError, match="stop_generation"):
            await conn.execute(
                "UPDATE position SET stop_generation = 0 WHERE id = $1", seeded["position_id"]
            )
        generation = await conn.fetchval(
            "SELECT stop_generation FROM position WHERE id = $1", seeded["position_id"]
        )
    assert generation == 1


async def test_a_long_stop_at_or_above_the_last_price_is_refused(
    pool: asyncpg.Pool, seeded: dict[str, object]
) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError, match="last known market"):
            await conn.execute(
                "UPDATE position SET stop_price = 1010 WHERE id = $1", seeded["position_id"]
            )
        assert await _stop(conn, seeded["position_id"]) == Decimal("950")


async def test_tightening_up_still_below_the_last_price_is_allowed(
    pool: asyncpg.Pool, seeded: dict[str, object]
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE position SET stop_price = 990, stop_generation = 2 WHERE id = $1",
            seeded["position_id"],
        )
        assert await _stop(conn, seeded["position_id"]) == Decimal("990")
