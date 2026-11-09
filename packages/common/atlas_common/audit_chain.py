"""The write half of the audit chain, for callers that hold an asyncpg connection.

``atlas_execution.audit`` writes through SQLAlchemy; the API gateway keeps an asyncpg pool.
Both must produce byte-identical rows, so the parts that decide those bytes - the record
types, the advisory-lock key, the payload rules, the hash formula - live here, and both
writers import them. Two copies of the hash formula would eventually disagree, and a chain
that disagrees with itself is worse than no chain.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from atlas_common.hashchain import GENESIS_HASH, AuditEntry, canonical_payload, compute_hash

RecordType = Literal[
    "DECISION",
    "ORDER",
    "MODE_CHANGE",
    "CONFIG_CHANGE",
    "KILL_SWITCH",
    "PROMOTION",
    "INCIDENT",
    "RECONCILE",
]

RECORD_TYPES: frozenset[str] = frozenset(
    {
        "DECISION",
        "ORDER",
        "MODE_CHANGE",
        "CONFIG_CHANGE",
        "KILL_SWITCH",
        "PROMOTION",
        "INCIDENT",
        "RECONCILE",
    }
)

# Arbitrary but fixed: one 64-bit key so every writer serialises on the same lock.
CHAIN_LOCK_KEY = 0x41544C4153554449

INSERT_SQL = (
    "INSERT INTO audit_record (seq, record_type, payload, prev_hash, hash)"
    " VALUES ($1, $2::audit_record_type, CAST($3 AS jsonb), $4, $5)"
)
HEAD_SQL = "SELECT seq, hash FROM audit_record ORDER BY seq DESC LIMIT 1"


def validate(record_type: str, payload: Mapping[str, Any]) -> None:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown audit record_type {record_type!r}")
    if not isinstance(payload, Mapping):
        raise TypeError("audit payload must be a mapping")
    if "seq" in payload or "hash" in payload:
        raise ValueError("audit payload may not carry chain fields")


def next_entry(last_seq: int, last_hash: str, payload: Mapping[str, Any]) -> AuditEntry:
    seq = last_seq + 1
    return AuditEntry(
        seq=seq,
        payload=dict(payload),
        prev_hash=last_hash,
        hash=compute_hash(last_hash, dict(payload), seq),
    )


def head(row: Mapping[str, Any] | None) -> tuple[int, str]:
    if row is None:
        return 0, GENESIS_HASH
    return int(row["seq"]), str(row["hash"])


async def append(conn: Any, record_type: str, payload: Mapping[str, Any]) -> tuple[int, str]:
    """Append one record and return ``(seq, hash)``.

    The caller owns the transaction: the advisory lock is released at commit, so the row and
    whatever caused it are committed or rolled back together.
    """
    validate(record_type, payload)
    await conn.execute("SELECT pg_advisory_xact_lock($1)", CHAIN_LOCK_KEY)  # nosemgrep
    entry = next_entry(*head(await conn.fetchrow(HEAD_SQL)), payload)  # nosemgrep
    await conn.execute(  # nosemgrep
        INSERT_SQL,
        entry.seq,
        record_type,
        canonical_payload(entry.payload).decode("utf-8"),
        entry.prev_hash,
        entry.hash,
    )
    return entry.seq, entry.hash
