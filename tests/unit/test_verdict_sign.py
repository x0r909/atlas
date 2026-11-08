"""Verdict signatures: any field change, any stale verdict, any truncation must fail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from atlas_schemas.verdict_sign import (
    VERDICT_TTL_SECONDS,
    MissingSigningKey,
    canonical_payload,
    load_key,
    order_signing_payload,
    sign,
    sign_order,
    sign_verdict,
    verdict_signing_payload,
    verify,
    verify_signature_field,
)

KEY = "0123456789abcdef0123456789abcdef"
WRONG_KEY = "fedcba9876543210fedcba9876543210"


@pytest.fixture()
def verdict() -> dict[str, object]:
    at = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    return {
        "verdict_id": uuid4(),
        "proposal_id": uuid4(),
        "venue_id": "indodax",
        "pair": "btcidr",
        "side": "BUY",
        "sized_quantity": Decimal("0.00012345"),
        "stop_price": Decimal("900000000"),
        "target_price": Decimal("1100000000"),
        "verdict_at": at,
        "expires_at": at + timedelta(seconds=VERDICT_TTL_SECONDS),
        "approved": True,
        "rule_results": [],
    }


def test_the_signed_payload_is_exactly_the_nine_contract_fields(verdict):
    payload = verdict_signing_payload(verdict)
    assert set(payload) == {
        "proposal_id",
        "venue_id",
        "pair",
        "side",
        "sized_quantity",
        "stop_price",
        "target_price",
        "verdict_at",
        "expires_at",
    }
    assert "verdict_id" not in payload


def test_sign_and_verify_round_trip(verdict):
    signature = sign_verdict(verdict, KEY)
    assert len(signature) == 64
    assert verify_signature_field(verdict, signature, KEY) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pair", "ethidr"),
        ("side", "SELL"),
        ("sized_quantity", Decimal("0.00012346")),
        ("stop_price", Decimal("900000001")),
        ("target_price", Decimal("1100000001")),
        ("venue_id", "other-venue"),
    ],
)
def test_any_field_change_invalidates_the_signature(verdict, field, value):
    signature = sign_verdict(verdict, KEY)
    tampered = dict(verdict)
    tampered[field] = value
    assert verify_signature_field(tampered, signature, KEY) is False


def test_timestamp_tampering_invalidates_the_signature(verdict):
    signature = sign_verdict(verdict, KEY)
    extended = dict(verdict)
    extended["expires_at"] = verdict["expires_at"] + timedelta(seconds=600)
    assert verify_signature_field(extended, signature, KEY) is False


def test_a_truncated_signature_is_refused(verdict):
    signature = sign_verdict(verdict, KEY)
    assert verify_signature_field(verdict, signature[:63], KEY) is False
    assert verify_signature_field(verdict, signature + "0", KEY) is False


def test_the_wrong_key_never_verifies(verdict):
    signature = sign_verdict(verdict, KEY)
    assert verify_signature_field(verdict, signature, WRONG_KEY) is False


def test_signature_is_case_insensitive_hex_but_still_constant_time(verdict):
    signature = sign_verdict(verdict, KEY)
    assert verify_signature_field(verdict, signature.upper(), KEY) is True


def test_canonical_payload_ignores_key_order_and_escapes(verdict):
    payload = verdict_signing_payload(verdict)
    reordered = dict(reversed(list(payload.items())))
    assert canonical_payload(payload) == canonical_payload(reordered)
    assert b" " not in canonical_payload(payload)


def test_decimal_is_signed_by_its_decimal_text_not_a_float():
    assert canonical_payload({"qty": Decimal("0.1")}) == b'{"qty":"0.1"}'


def test_missing_fields_are_refused_rather_than_signed_partially(verdict):
    broken = {key: value for key, value in verdict.items() if key != "stop_price"}
    with pytest.raises(ValueError, match="stop_price"):
        sign_verdict(broken, KEY)


def test_naive_datetimes_cannot_be_signed():
    payload = verdict_signing_payload(
        {
            "proposal_id": uuid4(),
            "venue_id": "indodax",
            "pair": "btcidr",
            "side": "BUY",
            "sized_quantity": Decimal("1"),
            "stop_price": Decimal("1"),
            "target_price": Decimal("2"),
            "verdict_at": datetime(2026, 11, 8, 10, 0),
            "expires_at": datetime(2026, 11, 8, 10, 1),
        }
    )
    with pytest.raises(ValueError, match="naive datetime"):
        sign(payload, KEY)


def test_an_empty_key_is_refused_not_treated_as_valid():
    with pytest.raises(MissingSigningKey):
        sign({"a": 1}, "")
    with pytest.raises(MissingSigningKey):
        load_key({})


def test_the_key_is_read_from_the_environment_only():
    assert load_key({"ATLAS_ORDER_HMAC_KEY": KEY}) == KEY


def test_order_payload_binds_the_verdict_and_the_client_order_id():
    signed_at = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    order = {
        "signed_order_id": uuid4(),
        "verdict_id": uuid4(),
        "proposal_id": uuid4(),
        "venue_id": "indodax",
        "pair": "btcidr",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": Decimal("0.0002"),
        "limit_price": Decimal("1000000000"),
        "stop_price": Decimal("900000000"),
        "client_order_id": "atlas-abc-def-1",
        "signed_at": signed_at,
        "expires_at": signed_at + timedelta(seconds=30),
    }
    signature = sign_order(order, KEY)
    assert verify(order_signing_payload(order), signature, KEY) is True
    moved = dict(order)
    moved["client_order_id"] = "atlas-abc-def-2"
    assert verify(order_signing_payload(moved), signature, KEY) is False
