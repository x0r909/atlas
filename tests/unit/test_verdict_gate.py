"""T064: the verdict-rejection matrix (FR-062, SC-020).

Every row is one way an order must be refused before a venue ever hears about it. The codes are
the contract's, so a caller can tell "expired" from "already used" without parsing prose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from atlas_execution.gate import GateInputs, Refused, decide, order_fields
from atlas_schemas.order import SignedOrder
from atlas_schemas.risk import RiskVerdict
from atlas_schemas.verdict_sign import sign_order_with_verdict

KEY = b"test-signing-key"
NOW = datetime(2024, 5, 1, 3, 0, tzinfo=UTC)
QUANTITY = Decimal("100")


def _verdict(**overrides):
    values = {
        "verdict_id": uuid4(),
        "proposal_id": uuid4(),
        "approved": True,
        "rule_results": [],
        "sized_quantity": QUANTITY,
        "risk_config_version": 1,
        "venue_profile_id": 1,
        "portfolio_snapshot_id": uuid4(),
        "input_hash": "f" * 64,
        "verdict_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=55),
        "signature": "a" * 64,
    }
    values.update(overrides)
    return RiskVerdict(**values)


def _order(verdict: RiskVerdict, *, side="BUY", quantity=QUANTITY, signature=None, **overrides):
    values = {
        "signed_order_id": uuid4(),
        "verdict_id": verdict.verdict_id,
        "proposal_id": verdict.proposal_id,
        "venue_id": "indodax",
        "pair": "BTCIDR",
        "side": side,
        "type": "LIMIT",
        "quantity": quantity,
        "limit_price": Decimal("1000"),
        "stop_price": Decimal("950"),
        "client_order_id": f"atlas-{uuid4().hex[:8]}-{uuid4().hex[:8]}-1",
        "signature": None,
        "signed_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=29),
    }
    values.update(overrides)
    unsigned = SignedOrder(**{**values, "signature": "0" * 64})
    values["signature"] = signature or sign_order_with_verdict(
        order_fields(unsigned), str(verdict.verdict_id), KEY
    )
    return SignedOrder(**values)


def _inputs(verdict, order, **overrides):
    values = {
        "verdict": verdict,
        "order": order,
        "mode": "AUTONOMOUS",
        "venue_live_enabled": False,
        "now": NOW,
        "consumed_at": None,
    }
    values.update(overrides)
    return GateInputs(**values)


def _refusal(inputs) -> str:
    with pytest.raises(Refused) as caught:
        decide(inputs, key=KEY)
    return caught.value.code


def test_a_valid_order_passes() -> None:
    verdict = _verdict()
    decide(_inputs(verdict, _order(verdict)), key=KEY)


def test_a_verdict_that_was_never_stored_is_unknown() -> None:
    verdict = _verdict()
    assert _refusal(_inputs(None, _order(verdict))) == "VERDICT_UNKNOWN_ID"


def test_a_verdict_expired_one_second_ago_is_refused() -> None:
    verdict = _verdict(
        expires_at=NOW - timedelta(seconds=1), verdict_at=NOW - timedelta(seconds=61)
    )
    order = _order(verdict)
    assert _refusal(_inputs(verdict, order)) == "VERDICT_EXPIRED"


def test_a_signed_order_past_its_own_lifetime_is_refused() -> None:
    verdict = _verdict()
    order = _order(
        verdict, expires_at=NOW - timedelta(seconds=1), signed_at=NOW - timedelta(seconds=31)
    )
    assert _refusal(_inputs(verdict, order)) == "VERDICT_EXPIRED"


def test_the_second_submission_of_the_same_verdict_is_refused() -> None:
    verdict = _verdict()
    order = _order(verdict)
    assert _refusal(_inputs(verdict, order, consumed_at=NOW - timedelta(seconds=2))) == (
        "VERDICT_CONSUMED"
    )


def test_a_rejected_verdict_is_never_tradable() -> None:
    verdict = _verdict(approved=False, signature=None, primary_rule_id="RR_BELOW_MIN")
    order = _order(verdict)
    assert _refusal(_inputs(verdict, order)) == "VERDICT_NOT_APPROVED"


def test_an_edited_quantity_breaks_the_signature() -> None:
    verdict = _verdict()
    signed = _order(verdict)
    tampered = signed.model_copy(update={"quantity": Decimal("500")})
    assert _refusal(_inputs(verdict, tampered)) == "VERDICT_SIGNATURE_INVALID"


def test_an_order_repointed_at_another_verdict_breaks_the_signature() -> None:
    verdict = _verdict()
    order = _order(verdict)
    swapped = order.model_copy(update={"verdict_id": uuid4()})
    assert _refusal(_inputs(verdict, swapped)) == "VERDICT_SIGNATURE_INVALID"


def test_a_quantity_the_verdict_did_not_size_is_a_payload_mismatch() -> None:
    verdict = _verdict(sized_quantity=Decimal("100"))
    order = _order(verdict, quantity=Decimal("120"))
    # Signed for 120, approved for 100: the signature holds, the numbers do not.
    assert _refusal(_inputs(verdict, order)) == "VERDICT_PAYLOAD_MISMATCH"


def test_a_halted_system_refuses_entries_but_never_blocks_an_exit() -> None:
    verdict = _verdict()
    assert _refusal(_inputs(verdict, _order(verdict), mode="HALTED")) == "MODE_NOT_TRADABLE"
    decide(_inputs(verdict, _order(verdict, side="SELL"), mode="HALTED"), key=KEY)


def test_paper_mode_may_not_open_a_position_on_a_live_enabled_venue() -> None:
    verdict = _verdict()
    order = _order(verdict)
    assert _refusal(_inputs(verdict, order, mode="PAPER", venue_live_enabled=True)) == (
        "MODE_NOT_TRADABLE"
    )
    decide(_inputs(verdict, order, mode="PAPER", venue_live_enabled=False), key=KEY)


def test_co_pilot_and_autonomous_may_trade_a_live_venue() -> None:
    for mode in ("CO_PILOT", "AUTONOMOUS"):
        verdict = _verdict()
        decide(_inputs(verdict, _order(verdict), mode=mode, venue_live_enabled=True), key=KEY)


def test_the_wrong_signing_key_is_refused() -> None:
    verdict = _verdict()
    order = _order(verdict)
    with pytest.raises(Refused) as caught:
        decide(_inputs(verdict, order), key=b"another-key")
    assert caught.value.code == "VERDICT_SIGNATURE_INVALID"
