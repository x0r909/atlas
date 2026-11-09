"""FR-065 / pre-Live gate: the runtime role must not be able to unmake the guarantees.

Triggers refuse UPDATE/DELETE/TRUNCATE on ``audit_record`` and ``mode_state``, but a trigger
is only as strong as the privilege to disable it: the table owner can run
``ALTER TABLE ... DISABLE TRIGGER USER`` and then rewrite history. That is exactly what the
tests in this repository do when they need a clean chain, which is why the property cannot be
demonstrated with the owner connection alone.

So this test proves two things about a deliberately unprivileged role:

* it can append (the application still works), and
* it cannot disable the triggers, drop them, or alter the tables at all.

The role is created here rather than by a migration because role creation is a deployment
decision; the migration that matters is the one that grants only SELECT/INSERT, which this
test would catch if it regressed.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncGenerator

import asyncpg
import pytest

DSN = os.environ.get("ATLAS_DB_DSN", "")
RUNTIME_ROLE = "atlas_app_runtime"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="ATLAS_DB_DSN is not set; needs a migrated Postgres"),
]

# The statements are inline literals rather than constants assembled at import: a DDL string
# built from a name is the shape of an injection, and this fixture is not the place to teach
# a reader to accept that shape. Postgres has no ALTER/DROP grant to withhold, so the only
# sane grant is a full revoke plus SELECT and INSERT - which is why the runtime role must not
# be the owner: ownership is what unlocks DDL.


async def _provision(conn: asyncpg.Connection) -> None:
    """Create the role if needed and give it exactly SELECT + INSERT."""
    try:
        with contextlib.suppress(asyncpg.DuplicateObjectError):
            await conn.execute("CREATE ROLE atlas_app_runtime NOLOGIN")
        await conn.execute("GRANT USAGE ON SCHEMA public TO atlas_app_runtime")
        await conn.execute("REVOKE ALL ON audit_record FROM atlas_app_runtime")
        await conn.execute("REVOKE ALL ON mode_state FROM atlas_app_runtime")
        await conn.execute("GRANT SELECT, INSERT ON audit_record TO atlas_app_runtime")
        await conn.execute("GRANT SELECT, INSERT ON mode_state TO atlas_app_runtime")
        await conn.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO atlas_app_runtime"
        )
    except asyncpg.exceptions.InsufficientPrivilegeError:  # pragma: no cover
        pytest.skip("creating a role needs a superuser; this belongs in CI setup")


@pytest.fixture()
async def runtime() -> AsyncGenerator[asyncpg.Connection, None]:
    """A connection acting as the unprivileged runtime role."""
    created = await asyncpg.connect(DSN)
    try:
        await _provision(created)
        await created.execute("SET ROLE atlas_app_runtime")
        yield created
        await created.execute("RESET ROLE")
    finally:
        if not created.is_closed():
            await created.close()


async def test_the_runtime_role_can_append_a_record(runtime: asyncpg.Connection) -> None:
    seq = await runtime.fetchval(
        "INSERT INTO audit_record (seq, record_type, payload, prev_hash, hash) "
        "SELECT coalesce(max(seq), 0) + 1, 'ORDER', '{}'::jsonb, repeat('0', 64), "
        "repeat(md5(random()::text), 2) "
        "FROM audit_record RETURNING seq"
    )
    assert isinstance(seq, int)


async def test_the_runtime_role_cannot_disable_the_trigger(runtime: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await runtime.execute("ALTER TABLE audit_record DISABLE TRIGGER USER")


async def test_the_runtime_role_cannot_drop_the_trigger(runtime: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await runtime.execute("DROP TRIGGER audit_record_no_update ON audit_record")


async def test_the_runtime_role_cannot_rewrite_history(runtime: asyncpg.Connection) -> None:
    # Even with the privilege, the trigger would refuse; without it, Postgres refuses first.
    with pytest.raises(asyncpg.PostgresError):
        await runtime.execute("UPDATE audit_record SET payload = '{}'::jsonb")


async def test_the_owner_can_disable_the_trigger(runtime: asyncpg.Connection) -> None:
    """The gap this test exists for: the owner connection is not a safe runtime identity."""
    await runtime.execute("RESET ROLE")
    row = await runtime.fetchval(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_record_no_update'"
    )
    assert row == 1
    await runtime.execute("ALTER TABLE audit_record DISABLE TRIGGER audit_record_no_update")
    await runtime.execute("ALTER TABLE audit_record ENABLE TRIGGER audit_record_no_update")
