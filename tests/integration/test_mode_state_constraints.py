"""The rollout graph must be enforced by Postgres, not only by the API.

Run against a real database:
    ATLAS_DB_DSN=postgresql://atlas:***@localhost:5432/atlas \
        pytest tests/integration/test_mode_state_constraints.py

Without a DSN these tests skip: a constraint that was never executed is not evidence.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from uuid import uuid4

import asyncpg
import pytest

DSN = os.environ.get("ATLAS_DB_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="ATLAS_DB_DSN is not set; constraint tests need a real Postgres"
)


def _dsn_for_asyncpg() -> str:
    return DSN.replace("postgresql+asyncpg://", "postgresql://")


async def _reset(connection: asyncpg.Connection) -> None:
    """Empty the rollout log.

    The append-only trigger refuses DELETE, which is exactly what the tests below assert,
    so it is disabled for the reset only and re-enabled before the fixture yields.
    """
    await connection.execute("ALTER TABLE mode_state DISABLE TRIGGER USER")
    await connection.execute("DELETE FROM mode_state")
    await connection.execute("ALTER TABLE mode_state ENABLE TRIGGER USER")
    await connection.execute(
        "INSERT INTO mode_state (mode, reason_code, actor) "
        "VALUES ('PAPER', 'STARTUP_DEFAULT', 'SYSTEM')"
    )


@pytest.fixture()
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    connection = await asyncpg.connect(_dsn_for_asyncpg())
    await _reset(connection)
    try:
        yield connection
    finally:
        await _reset(connection)
        await connection.close()


# A promotion carries an evidence reference by default; passing evidence=None is how a
# test asks for the "promotion without evidence" case.
_MINT_EVIDENCE = object()

# Every value travels as a positional parameter; the SQL itself is a constant.
_INSERT_MOVE = (
    "INSERT INTO mode_state (mode, previous_mode, reason_code, actor, capital_cap_idr,"
    " evidence_ref) VALUES ($1,"
    " (SELECT mode FROM mode_state ORDER BY created_at DESC, id DESC LIMIT 1),"
    " $2, $3, $4, $5)"
)


async def _move(
    connection: asyncpg.Connection,
    to_mode: str,
    *,
    reason: str = "OPERATOR_PROMOTION",
    actor: str = "OPERATOR",
    cap: int | None = None,
    evidence: object | None = _MINT_EVIDENCE,
) -> None:
    await connection.execute(
        _INSERT_MOVE,
        to_mode,
        reason,
        actor,
        cap,
        str(uuid4()) if evidence is _MINT_EVIDENCE else evidence,
    )


async def test_paper_to_copilot_to_autonomous_is_allowed(conn):
    await _move(conn, "CO_PILOT", cap=30000)
    await _move(conn, "AUTONOMOUS", evidence=str(uuid4()))
    rows = await conn.fetch("SELECT mode::text AS mode FROM mode_state ORDER BY created_at")
    assert [row["mode"] for row in rows] == ["PAPER", "CO_PILOT", "AUTONOMOUS"]


async def test_paper_to_autonomous_is_refused_at_the_database(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="not allowed"):
        await _move(conn, "AUTONOMOUS")


async def test_autonomous_requires_promotion_evidence(conn):
    await _move(conn, "CO_PILOT", cap=30000)
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="promotion evidence"):
        await _move(conn, "AUTONOMOUS", evidence=None)


async def test_demotion_back_to_paper_clears_the_cap(conn):
    await _move(conn, "CO_PILOT", cap=30000)
    await _move(conn, "PAPER", reason="OPERATOR_DEMOTION")
    row = await conn.fetchrow(
        "SELECT mode::text AS mode, capital_cap_idr FROM mode_state"
        " ORDER BY created_at DESC LIMIT 1"
    )
    assert row["mode"] == "PAPER"
    assert row["capital_cap_idr"] is None


async def test_copilot_requires_a_cap_and_the_cap_is_capped(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _move(conn, "CO_PILOT", cap=None)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _move(conn, "CO_PILOT", cap=30001)


async def test_any_mode_can_halt_but_only_halted_states_can_resume(conn):
    await _move(conn, "HALTED", reason="KILL_SWITCH")
    await _move(conn, "PAPER", reason="RECOVERY")
    await _move(conn, "HALTED", reason="KILL_SWITCH")
    await _move(conn, "CO_PILOT", reason="RECOVERY", cap=30000)


async def test_halted_cannot_jump_straight_to_autonomous(conn):
    await _move(conn, "HALTED", reason="KILL_SWITCH")
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="not allowed"):
        await _move(conn, "AUTONOMOUS", reason="RECOVERY")


async def test_a_repeated_mode_is_refused(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="already the current"):
        await _move(conn, "PAPER", reason="RECOVERY")


async def test_previous_mode_must_name_the_actual_previous_mode(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="does not match"):
        await conn.execute(
            "INSERT INTO mode_state (mode, previous_mode, reason_code, actor, capital_cap_idr)"
            " VALUES ('CO_PILOT', 'AUTONOMOUS', 'OPERATOR_PROMOTION', 'OPERATOR', 30000)"
        )


async def test_promotion_without_evidence_is_refused(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="requires promotion evidence"):
        await conn.execute(
            "INSERT INTO mode_state (mode, previous_mode, reason_code, actor, capital_cap_idr)"
            " VALUES ('CO_PILOT', 'PAPER', 'OPERATOR_PROMOTION', 'OPERATOR', 30000)"
        )


async def test_rows_are_never_updated_or_deleted(conn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="append-only"):
        await conn.execute("UPDATE mode_state SET reason_code = 'RECOVERY'")
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="append-only"):
        await conn.execute("DELETE FROM mode_state")


async def test_the_first_row_cannot_be_anything_but_paper(conn):
    # Emptying the log is the one operation the append-only trigger forbids, so the
    # harness performs it with the triggers off and then re-arms them.
    await conn.execute("ALTER TABLE mode_state DISABLE TRIGGER USER")
    await conn.execute("DELETE FROM mode_state")
    await conn.execute("ALTER TABLE mode_state ENABLE TRIGGER USER")
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="first row must be PAPER"):
        await conn.execute(
            "INSERT INTO mode_state (mode, reason_code, actor) "
            "VALUES ('CO_PILOT', 'STARTUP_DEFAULT', 'SYSTEM')"
        )
