"""The audit chain must prove tampering, not merely look plausible."""

from __future__ import annotations

from atlas_common.hashchain import (
    GENESIS_HASH,
    AuditChain,
    AuditEntry,
    compute_hash,
    verify_chain,
    verify_entry,
)


def _chain(length: int = 3) -> AuditChain:
    chain = AuditChain()
    for index in range(length):
        chain.append({"kind": "order.accepted", "n": index, "amount_idr": "250000"})
    return chain


def test_genesis_links_to_the_first_record_and_the_chain_verifies():
    chain = _chain()
    first = chain.entries[0]
    assert first.seq == 1
    assert first.prev_hash == GENESIS_HASH
    assert first.hash == compute_hash(GENESIS_HASH, first.payload, 1)
    assert chain.verify().ok is True
    assert chain.verify().checked == 3


def test_hash_formula_is_pinned():
    """Anyone reimplementing the writer must produce these exact bytes."""
    import hashlib

    payload = {"a": 1, "b": "x"}
    digest = hashlib.sha256()
    digest.update(b"")
    digest.update(b'{"a":1,"b":"x"}')
    digest.update((1).to_bytes(8, "big"))
    assert compute_hash(None, payload, 1) == digest.hexdigest()


def test_mutating_a_closed_record_breaks_verification_at_that_seq():
    chain = _chain()
    entries = list(chain.entries)
    tampered = AuditEntry(
        seq=entries[1].seq,
        payload={"kind": "order.accepted", "n": 999},
        prev_hash=entries[1].prev_hash,
        hash=entries[1].hash,
    )
    entries[1] = tampered
    result = verify_chain(entries)
    assert result.ok is False
    assert result.broken_at_seq == 2
    assert "hash mismatch" in (result.reason or "")


def test_rewriting_payload_and_hash_still_breaks_the_next_link():
    chain = _chain()
    entries = list(chain.entries)
    forged_hash = compute_hash(entries[0].prev_hash, {"kind": "forged"}, 1)
    entries[0] = AuditEntry(1, {"kind": "forged"}, GENESIS_HASH, forged_hash)
    result = verify_chain(entries)
    assert result.ok is False
    assert result.broken_at_seq == 2
    assert "prev_hash" in (result.reason or "")


def test_a_deleted_record_is_reported_as_a_gap():
    entries = list(_chain().entries)
    result = verify_chain([entries[0], entries[2]])
    assert result.ok is False
    assert result.broken_at_seq == 3
    assert "gap" in (result.reason or "")


def test_decimal_and_timestamp_serialisation_is_stable_across_runs():
    from datetime import UTC, datetime
    from decimal import Decimal

    payload = {"price": Decimal("0.1"), "at": datetime(2026, 11, 8, 10, 0, tzinfo=UTC)}
    first = compute_hash(GENESIS_HASH, payload, 1)
    assert first == compute_hash(GENESIS_HASH, dict(reversed(list(payload.items()))), 1)


def test_naive_timestamps_are_refused_rather_than_signed_as_local_time():
    from datetime import datetime

    import pytest

    with pytest.raises(ValueError, match="UTC-aware"):
        compute_hash(GENESIS_HASH, {"at": datetime(2026, 11, 8, 10, 0)}, 1)


def test_single_record_verification_reports_the_problem():
    chain = _chain(1)
    assert verify_entry(chain.entries[0]) is None
    broken = AuditEntry(1, {"x": 1}, GENESIS_HASH, "0" * 64)
    assert "hash mismatch" in (verify_entry(broken) or "")
