"""Audit append-only + chain integrity, against a real Postgres.

The writer is the only legal path; the tests prove both halves of that claim - the chain
detects an edit, and an application role cannot edit at all.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from atlas_common.dsn import to_async_dsn
from atlas_common.hashchain import GENESIS_HASH, verify_chain
from atlas_execution.audit import append, load_chain, next_entry, verify
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

DSN = os.environ.get("ATLAS_DB_DSN", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="ATLAS_DB_DSN is not set; needs a migrated Postgres"),
]


async def _empty(connection: AsyncConnection) -> None:
    """TRUNCATE is refused by audit_record_no_truncate; a test fixture may lift the trigger
    it is about to prove. Nothing else in the suite touches audit_record."""
    await connection.execute(text("ALTER TABLE audit_record DISABLE TRIGGER USER"))
    # RESTART IDENTITY: the chain is only gapless from 1 if the counter restarts too.
    await connection.execute(text("TRUNCATE audit_record RESTART IDENTITY"))
    await connection.execute(text("ALTER TABLE audit_record ENABLE TRIGGER USER"))


@pytest.fixture()
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    created = create_async_engine(to_async_dsn(DSN))
    # begin(), not connect(): an implicit transaction is rolled back on close, which would
    # make the clean-up below a no-op and let rows from another test survive into this one.
    async with created.begin() as connection:
        exists = await connection.execute(text("SELECT to_regclass('public.audit_record')"))
        if exists.scalar() is None:
            await created.dispose()
            pytest.skip("audit_record does not exist; run `alembic upgrade head` first")
        await _empty(connection)
    try:
        yield created
    finally:
        async with created.begin() as connection:
            await _empty(connection)
        await created.dispose()


@pytest.fixture()
async def conn(engine: AsyncEngine) -> AsyncGenerator[AsyncConnection, None]:
    async with engine.begin() as connection:
        yield connection


async def test_first_record_links_to_genesis(conn: AsyncConnection):
    result = await append(conn, "ORDER", {"order_id": "a", "status": "PENDING_NEW"})
    assert result.seq == 1
    assert result.entry.prev_hash == GENESIS_HASH
    assert (await verify(conn)).ok is True


async def test_records_are_gapless_and_ordered(conn: AsyncConnection):
    for index in range(5):
        await append(conn, "DECISION", {"cycle": index})
    chain = await load_chain(conn)
    assert [entry.seq for entry in chain] == [1, 2, 3, 4, 5]
    assert verify_chain(chain).checked == 5


async def test_next_entry_is_the_pure_step_of_the_same_formula(conn: AsyncConnection):
    await append(conn, "ORDER", {"n": 1})
    chain = await load_chain(conn)
    expected = next_entry(chain[0].seq, chain[0].hash, {"n": 2})
    await append(conn, "ORDER", {"n": 2})
    assert (await load_chain(conn))[1] == expected


async def test_an_audit_row_cannot_be_edited(conn: AsyncConnection):
    await append(conn, "ORDER", {"amount_idr": "250000"})
    with pytest.raises(IntegrityError, match="append-only: UPDATE"):
        await conn.execute(
            text("UPDATE audit_record SET payload = CAST(:payload AS jsonb) WHERE seq = 1"),
            {"payload": '{"amount_idr":"1"}'},
        )


async def test_an_audit_row_cannot_be_deleted(conn: AsyncConnection):
    for index in range(3):
        await append(conn, "INCIDENT", {"n": index})
    with pytest.raises(IntegrityError, match="append-only: DELETE"):
        await conn.execute(text("DELETE FROM audit_record WHERE seq = 2"))


async def test_verification_still_detects_tampering_that_lifts_the_trigger(conn: AsyncConnection):
    """The trigger is the first line; verify() is the second, for an owner who disables it."""
    await append(conn, "ORDER", {"amount_idr": "250000"})
    await append(conn, "ORDER", {"amount_idr": "300000"})
    await conn.execute(text("ALTER TABLE audit_record DISABLE TRIGGER USER"))
    await conn.execute(
        text("UPDATE audit_record SET payload = CAST(:payload AS jsonb) WHERE seq = 1"),
        {"payload": '{"amount_idr":"1"}'},
    )
    await conn.execute(text("ALTER TABLE audit_record ENABLE TRIGGER USER"))
    result = await verify(conn)
    assert result.ok is False
    assert result.broken_at_seq == 1
    assert "hash mismatch" in (result.reason or "")


async def test_a_gap_is_reported_even_if_the_remaining_rows_chain(conn: AsyncConnection):
    for index in range(3):
        await append(conn, "INCIDENT", {"n": index})
    await conn.execute(text("ALTER TABLE audit_record DISABLE TRIGGER USER"))
    await conn.execute(text("DELETE FROM audit_record WHERE seq = 2"))
    await conn.execute(text("ALTER TABLE audit_record ENABLE TRIGGER USER"))
    result = await verify(conn)
    assert result.ok is False
    assert result.broken_at_seq == 3
    assert "gap" in (result.reason or "")


async def test_unknown_record_types_and_injected_chain_fields_are_refused(
    conn: AsyncConnection,
):
    with pytest.raises(ValueError, match="unknown audit record_type"):
        await append(conn, "NOT_A_TYPE", {})
    with pytest.raises(ValueError, match="chain fields"):
        await append(conn, "ORDER", {"seq": 99})
    with pytest.raises(TypeError):
        await append(conn, "ORDER", ["not", "a", "mapping"])  # type: ignore[arg-type]


async def test_the_table_shape_matches_the_writer(conn: AsyncConnection):
    columns = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'audit_record'"
            )
        )
    ).scalars()
    assert {"seq", "record_type", "payload", "prev_hash", "hash", "created_at"} <= set(columns)
