"""FR-065: every risk-config change is a key-level audit record, not a silent rewrite.

The audit log is the only place a later reader can learn that the risk-per-trade went from
0.5 % to 4 %, who did it, and when. So the properties under test are: one record per changed
key, old and new values both present, the chain still verifies, and an invalid change leaves
no trace at all (no half-applied config, no audit entry describing something that never
happened).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
import pytest
from atlas_api.repo import AsyncpgRepo

DSN = os.environ.get("ATLAS_DB_DSN")


@pytest.fixture()
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    if not DSN:
        pytest.skip("ATLAS_DB_DSN not set")
    created = await asyncpg.create_pool(DSN)
    async with created.acquire() as connection:
        if await connection.fetchval("SELECT to_regclass('public.risk_config')") is None:
            await created.close()
            pytest.skip("risk_config does not exist; run `alembic upgrade head` first")
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture()
async def repo(pool: asyncpg.Pool) -> AsyncpgRepo:
    return AsyncpgRepo(pool)


async def _max_seq(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as connection:
        return int(await connection.fetchval("SELECT coalesce(max(seq), 0) FROM audit_record"))


async def _head(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow("SELECT * FROM risk_config ORDER BY version DESC LIMIT 1")
    assert row is not None
    return dict(row)


def _decoded(payload: Any) -> dict[str, Any]:
    """asyncpg hands jsonb back as text unless a codec is registered on the pool."""
    if isinstance(payload, str):
        decoded = json.loads(payload)
        return decoded if isinstance(decoded, dict) else {}
    return dict(payload)


async def _audit_tail(pool: asyncpg.Pool, after_seq: int) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT seq, record_type, payload FROM audit_record WHERE seq > $1 ORDER BY seq",
            after_seq,
        )
    return [
        {"seq": row["seq"], "record_type": row["record_type"], "payload": _decoded(row["payload"])}
        for row in rows
    ]


async def test_a_single_key_change_bumps_the_version_and_writes_one_record(
    pool: asyncpg.Pool, repo: AsyncpgRepo
) -> None:
    before = await _head(pool)
    base_seq = await _max_seq(pool)
    target = 4 if before["max_open_positions"] != 4 else 5
    await repo.apply_risk_config_changes({"max_open_positions": target}, operator="augie")
    after = await _head(pool)

    assert after["version"] == before["version"] + 1
    assert after["max_open_positions"] == target
    # Everything else is carried forward, not reset to a default.
    assert after["risk_per_trade_pct"] == before["risk_per_trade_pct"]

    tail = await _audit_tail(pool, base_seq)
    assert tail, "the change wrote nothing to the audit log"
    records = [row for row in tail if row["record_type"] == "CONFIG_CHANGE"]
    assert records, "the config change wrote no audit record"
    last = records[-1]["payload"]
    assert last["key"] == "max_open_positions"
    assert last["old"] == before["max_open_positions"]
    assert last["new"] == target
    assert last["operator"] == "augie"

    assert str(records[-1]["payload"]["at"])


async def test_two_keys_produce_two_records_and_one_version(
    pool: asyncpg.Pool, repo: AsyncpgRepo
) -> None:
    before = await _head(pool)
    base_seq = await _max_seq(pool)
    positions = 3 if before["max_open_positions"] != 3 else 2
    orders = 4 if before["max_orders_per_hour"] != 4 else 5
    result = await repo.apply_risk_config_changes(
        {"max_open_positions": positions, "max_orders_per_hour": orders}, operator="augie"
    )

    assert result["version"] == before["version"] + 1
    assert sorted(result["changed"]) == ["max_open_positions", "max_orders_per_hour"]

    tail = await _audit_tail(pool, base_seq)
    records = [row for row in tail if row["record_type"] == "CONFIG_CHANGE"]
    keys = {row["payload"]["key"] for row in records}
    assert keys == {"max_open_positions", "max_orders_per_hour"}


async def test_a_value_that_did_not_change_writes_nothing(
    pool: asyncpg.Pool, repo: AsyncpgRepo
) -> None:
    before = await _head(pool)
    base_seq = await _max_seq(pool)
    result = await repo.apply_risk_config_changes(
        {"max_open_positions": before["max_open_positions"]}, operator="augie"
    )

    assert result["version"] == before["version"]
    assert result["changed"] == []
    assert (await _head(pool))["version"] == before["version"]
    assert await _audit_tail(pool, base_seq) == []


async def test_an_unknown_key_is_refused_before_anything_is_written(
    pool: asyncpg.Pool, repo: AsyncpgRepo
) -> None:
    before = await _head(pool)
    with pytest.raises(ValueError, match="unknown risk config key"):
        await repo.apply_risk_config_changes({"max_leverage": 5}, operator="augie")
    assert (await _head(pool))["version"] == before["version"]


async def test_a_change_that_breaks_a_constraint_writes_no_audit_record(
    pool: asyncpg.Pool, repo: AsyncpgRepo
) -> None:
    before = await _head(pool)
    # 500 % is inside numeric(6,3) but outside the upper-bound check.
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await repo.apply_risk_config_changes({"max_daily_loss_pct": 500}, operator="augie")

    assert (await _head(pool))["version"] == before["version"]
    async with pool.acquire() as connection:
        count = await connection.fetchval(
            "SELECT count(*) FROM audit_record"
            " WHERE payload->>'key' = 'max_daily_loss_pct' AND payload->>'new' = '500'"
        )
    assert count == 0
