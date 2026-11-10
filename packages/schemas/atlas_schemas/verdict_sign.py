"""Verdict and order signatures.

The Risk Engine holds the only signing key; the Execution Service holds only the verification
key. Nothing else in the system may mint an order: an order that does not carry a signature
over the exact verdict the Risk Engine produced is refused before it reaches a venue.

Canonicalisation is JSON with sorted keys, no whitespace and no ASCII escaping, so the bytes
signed are the same on every host and in every language.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

__all__ = [
    "KEY_ENV_VAR",
    "ORDER_TTL_SECONDS",
    "VERDICT_TTL_SECONDS",
    "MissingSigningKey",
    "canonical_payload",
    "order_signing_payload",
    "sign",
    "sign_order",
    "sign_verdict",
    "verdict_signing_payload",
    "verify",
    "verify_signature_field",
]

KEY_ENV_VAR = "ATLAS_ORDER_HMAC_KEY"
VERDICT_TTL_SECONDS = 60
ORDER_TTL_SECONDS = 30


class MissingSigningKey(RuntimeError):
    """Raised when no key is configured. Never silently sign with an empty key."""


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        # str(), not float(): 0.1 must sign as "0.1" and verify identically everywhere.
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("cannot sign a naive datetime; timestamps are UTC-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (UUID, date)):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not canonicalisable")


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    """Deterministic bytes for signing. Key order, None and Unicode are all fixed."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def _key_bytes(key: str | bytes) -> bytes:
    if isinstance(key, bytes):
        if not key:
            raise MissingSigningKey("signing key is empty")
        return key
    if not key:
        raise MissingSigningKey("signing key is empty")
    return key.encode("utf-8")


def sign(payload: Mapping[str, Any], key: str | bytes) -> str:
    return hmac.new(_key_bytes(key), canonical_payload(payload), hashlib.sha256).hexdigest()


def verify(payload: Mapping[str, Any], signature: str, key: str | bytes) -> bool:
    expected = sign(payload, key)
    return hmac.compare_digest(expected, signature.lower())


def load_key(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    key = env.get(KEY_ENV_VAR, "")
    if not key:
        raise MissingSigningKey(f"{KEY_ENV_VAR} is not set")
    return key


VERDICT_FIELDS: tuple[str, ...] = (
    "proposal_id",
    "venue_id",
    "pair",
    "side",
    "sized_quantity",
    "stop_price",
    "target_price",
    "verdict_at",
    "expires_at",
)


def verdict_signing_payload(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The exact nine fields the contract binds, and nothing else.

    Deliberately excludes verdict_id and rule_results: those are covered by the audit record,
    while these nine are what an order can be checked against.
    """
    missing = [field for field in VERDICT_FIELDS if field not in verdict]
    if missing:
        raise ValueError(f"verdict payload missing signed fields: {', '.join(missing)}")
    return {field: verdict[field] for field in VERDICT_FIELDS}


def sign_verdict(verdict: Mapping[str, Any], key: str | bytes) -> str:
    return sign(verdict_signing_payload(verdict), key)


def verify_signature_field(verdict: Mapping[str, Any], signature: str, key: str | bytes) -> bool:
    return verify(verdict_signing_payload(verdict), signature, key)


ORDER_FIELDS: tuple[str, ...] = (
    "signed_order_id",
    "verdict_id",
    "proposal_id",
    "venue_id",
    "pair",
    "side",
    "type",
    "quantity",
    "limit_price",
    "stop_price",
    "client_order_id",
    "signed_at",
    "expires_at",
)


def order_signing_payload(order: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in ORDER_FIELDS if field not in order]
    if missing:
        raise ValueError(f"order payload missing signed fields: {', '.join(missing)}")
    return {field: order[field] for field in ORDER_FIELDS}


def sign_order(order: Mapping[str, Any], key: str | bytes) -> str:
    return sign(order_signing_payload(order), key)


def _order_message(order: Mapping[str, Any], verdict_id: str) -> bytes:
    """Contract §5: the message is the canonical payload followed by the verdict id.

    Concatenating the id rather than including it in the mapping means an order cannot be
    re-pointed at another verdict by editing a field: the bytes change at the end.
    """
    return canonical_payload(order_signing_payload(order)) + verdict_id.encode("utf-8")


def sign_order_with_verdict(order: Mapping[str, Any], verdict_id: str, key: str | bytes) -> str:
    return hmac.new(_key_bytes(key), _order_message(order, verdict_id), hashlib.sha256).hexdigest()


def verify_order_with_verdict(
    order: Mapping[str, Any], verdict_id: str, signature: str, key: str | bytes
) -> bool:
    expected = sign_order_with_verdict(order, verdict_id, key)
    return hmac.compare_digest(expected, signature.lower())
