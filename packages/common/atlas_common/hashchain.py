"""Tamper-evident audit chain.

``hash = sha256(domain ‖ record_type ‖ 0x00 ‖ prev_hash_bytes ‖ canonical_payload ‖ seq_be64)``.

``record_type`` is inside the digest on purpose: without it a ``KILL_SWITCH`` record could be
relabelled ``INCIDENT`` with an UPDATE and still verify, which is exactly the edit an operator
who wants to hide a halt would make.

Each record commits to its predecessor, so editing a closed record breaks every hash after
it. Verification reports the first broken sequence number rather than a boolean: an operator
needs to know which record was touched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

__all__ = [
    "DOMAIN",
    "GENESIS_HASH",
    "AuditChain",
    "AuditEntry",
    "ChainVerification",
    "canonical_payload",
    "compute_hash",
    "verify_chain",
    "verify_entry",
]

GENESIS_HASH = "00" * 32
# Domain separation, so a hash from any other structure can never be reused here.
DOMAIN = b"atlas.audit.v2\x00"


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("audit payload timestamps must be UTC-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (UUID, date)):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not canonicalisable")


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    """Sorted-key, whitespace-free JSON. The same bytes are produced by every service."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def _prev_bytes(prev_hash: str | None) -> bytes:
    if not prev_hash:
        return b""
    if prev_hash == GENESIS_HASH:
        # The genesis marker is a literal, not a hash of anything; the chain starts empty.
        return b""
    try:
        return bytes.fromhex(prev_hash)
    except ValueError as exc:
        raise ValueError("prev_hash must be a hex-encoded sha256") from exc


def compute_hash(
    prev_hash: str | None,
    payload: Mapping[str, Any],
    seq: int,
    *,
    record_type: str,
) -> str:
    if seq < 1:
        raise ValueError("audit seq starts at 1")
    if not record_type:
        raise ValueError("record_type must not be empty; it is part of the digest")
    digest = hashlib.sha256()
    digest.update(DOMAIN)
    digest.update(record_type.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(_prev_bytes(prev_hash))
    digest.update(canonical_payload(payload))
    digest.update(seq.to_bytes(8, "big", signed=False))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: int
    record_type: str
    payload: Mapping[str, Any]
    prev_hash: str
    hash: str
    # The exact bytes that were hashed. jsonb re-normalises numbers (1e20 comes back as an
    # integer), so a writer that hashes the jsonb round-trip breaks on honest records.
    payload_canonical: str | None = None


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    checked: int
    broken_at_seq: int | None = None
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def verify_entry(entry: AuditEntry) -> str | None:
    stored = entry.payload_canonical
    if stored is not None and stored != canonical_payload(entry.payload).decode("utf-8"):
        return (
            f"seq {entry.seq}: payload does not match its stored canonical form "
            "(jsonb was rewritten, or the row came from another writer)"
        )
    expected = compute_hash(
        entry.prev_hash, entry.payload, entry.seq, record_type=entry.record_type
    )
    if expected != entry.hash:
        return f"seq {entry.seq}: hash mismatch (record altered or payload rewritten)"
    return None


def verify_chain(entries: Iterable[AuditEntry], *, start_seq: int = 1) -> ChainVerification:
    """Walk a gapless chain from ``start_seq``; the first defect stops verification."""
    previous = GENESIS_HASH
    expected_seq = start_seq
    checked = 0
    for entry in entries:
        if entry.seq != expected_seq:
            return ChainVerification(
                ok=False,
                checked=checked,
                broken_at_seq=entry.seq,
                reason=f"gap: expected seq {expected_seq}, got {entry.seq}",
            )
        if entry.prev_hash != previous:
            return ChainVerification(
                ok=False,
                checked=checked,
                broken_at_seq=entry.seq,
                reason=f"seq {entry.seq}: prev_hash does not match predecessor",
            )
        problem = verify_entry(entry)
        if problem is not None:
            return ChainVerification(
                ok=False, checked=checked, broken_at_seq=entry.seq, reason=problem
            )
        previous = entry.hash
        expected_seq += 1
        checked += 1
    return ChainVerification(ok=True, checked=checked)


class AuditChain:
    """In-memory appender used by the writer and by tests; Postgres holds the real chain."""

    def __init__(self, *, seq: int = 0, last_hash: str = GENESIS_HASH) -> None:
        self._seq = seq
        self._last_hash = last_hash
        self._entries: list[AuditEntry] = []

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def append(self, payload: Mapping[str, Any], *, record_type: str = "ORDER") -> AuditEntry:
        seq = self._seq + 1
        digest = compute_hash(self._last_hash, payload, seq, record_type=record_type)
        entry = AuditEntry(
            seq=seq,
            record_type=record_type,
            payload=payload,
            prev_hash=self._last_hash,
            hash=digest,
            payload_canonical=canonical_payload(payload).decode("utf-8"),
        )
        self._entries.append(entry)
        self._seq = seq
        self._last_hash = digest
        return entry

    def verify(self) -> ChainVerification:
        return verify_chain(self._entries)
