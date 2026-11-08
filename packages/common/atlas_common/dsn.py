"""Do not hand a synchronous DSN to a process that opens an async connection.

Every component in ATLAS talks to Postgres through an async driver, but the URL an
operator exports is the ordinary ``postgresql://`` form (that is what ``psql`` and the
compose stack use). SQLAlchemy picks the driver from the scheme, so a plain URL selects
psycopg and fails at connect time with ``ModuleNotFoundError: No module named 'psycopg'``.
Normalising once keeps the exported DSN unchanged and the failure impossible.
"""

from __future__ import annotations

ASYNC_DRIVER = "asyncpg"
_SYNC_SCHEMES = ("postgresql", "postgres")


def to_async_dsn(url: str) -> str:
    """Return ``url`` with the asyncpg driver, leaving an explicit driver untouched."""
    for scheme in _SYNC_SCHEMES:
        if url.startswith(f"{scheme}://"):
            return f"{scheme}+{ASYNC_DRIVER}://{url[len(scheme) + 3 :]}"
    return url


def to_plain_dsn(url: str) -> str:
    """Return ``url`` in the form asyncpg's own ``connect`` accepts."""
    return url.replace(f"postgresql+{ASYNC_DRIVER}://", "postgresql://").replace(
        f"postgres+{ASYNC_DRIVER}://", "postgresql://"
    )


__all__ = ["ASYNC_DRIVER", "to_async_dsn", "to_plain_dsn"]
