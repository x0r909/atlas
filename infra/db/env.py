"""Alembic environment: async engine, DSN from the environment, no ORM metadata.

The schema is hand-written SQL (constitution: money and order tables are reviewed line by
line), so `target_metadata` is ``None`` and autogenerate is intentionally unused. Running
``alembic revision --autogenerate`` produces an empty file rather than a plausible-looking
migration that nobody reviewed.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    url = os.environ.get("ATLAS_DB_DSN")
    if not url:
        raise SystemExit(
            "ATLAS_DB_DSN is not set. Export it before running alembic; the URL is never "
            "read from alembic.ini so a migration cannot target an unintended database."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (used to review a migration)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # TimescaleDB is not a vanilla Postgres dialect; without this alembic warns on
        # every hypertable operation.
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
