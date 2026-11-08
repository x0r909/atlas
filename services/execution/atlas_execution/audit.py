"""Audit writer: the only code path allowed to append to ``audit_record``.

Two properties matter and both are enforced here rather than trusted:

* **Gapless.** The next ``seq`` is read under a transaction-scoped advisory lock, so two
  concurrent writers cannot both claim ``n``. A gap is therefore evidence of tampering.
* **Chained.** ``hash = sha256(prev_hash ‖ canonical_payload ‖ seq)``; the previous hash is
  read from the last row, never supplied by the caller.

Callers must already be inside a transaction; the advisory lock is released at commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from atlas_common.hashchain import (
    GENESIS_HASH,
    AuditEntry,
    ChainVerification,
    canonical_payload,
    compute_hash,
    verify_chain,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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

# Arbitrary but fixed: derive a single 64-bit key for the audit chain.
CHAIN_LOCK_KEY = 0x41544C4153554449


@dataclass(frozen=True, slots=True)
class AppendResult:
    entry: AuditEntry

    @property
    def seq(self) -> int:
        return self.entry.seq

    @property
    def hash(self) -> str:
        return self.entry.hash


def next_entry(last_seq: int, last_hash: str, payload: dict[str, Any]) -> AuditEntry:
    """Pure step of the chain, so the formula can be tested without a database."""
    seq = last_seq + 1
    return AuditEntry(
        seq=seq,
        payload=payload,
        prev_hash=last_hash,
        hash=compute_hash(last_hash, payload, seq),
    )


def _validate(record_type: str, payload: dict[str, Any]) -> None:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown audit record_type {record_type!r}")
    if not isinstance(payload, dict):
        raise TypeError("audit payload must be a mapping")
    if "seq" in payload or "hash" in payload:
        raise ValueError("audit payload may not carry chain fields")


async def append(
    conn: AsyncConnection,
    record_type: RecordType | str,
    payload: dict[str, Any],
) -> AppendResult:
    """Append one record. Requires an open transaction."""
    _validate(record_type, payload)

    await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CHAIN_LOCK_KEY})
    row = (
        await conn.execute(text("SELECT seq, hash FROM audit_record ORDER BY seq DESC LIMIT 1"))
    ).first()

    last_seq = int(row[0]) if row is not None else 0
    last_hash = str(row[1]) if row is not None else GENESIS_HASH
    entry = next_entry(last_seq, last_hash, payload)

    await conn.execute(
        text(
            "INSERT INTO audit_record (seq, record_type, payload, prev_hash, hash) "
            "VALUES (:seq, :record_type, CAST(:payload AS jsonb), :prev_hash, :hash)"
        ),
        {
            "seq": entry.seq,
            "record_type": record_type,
            "payload": _json(entry.payload),
            "prev_hash": entry.prev_hash,
            "hash": entry.hash,
        },
    )
    return AppendResult(entry=entry)


def _json(payload: Mapping[str, Any]) -> str:
    return canonical_payload(payload).decode("utf-8")


async def load_chain(conn: AsyncConnection) -> list[AuditEntry]:
    rows = (
        await conn.execute(
            text("SELECT seq, payload, prev_hash, hash FROM audit_record ORDER BY seq")
        )
    ).all()
    return [
        AuditEntry(
            seq=int(row[0]),
            payload=dict(row[1]),
            prev_hash=str(row[2]),
            hash=str(row[3]),
        )
        for row in rows
    ]


async def verify(conn: AsyncConnection) -> ChainVerification:
    return verify_chain(await load_chain(conn))
