"""T051: the catalogue is a contract, so its order and ids are pinned, not discovered."""

from __future__ import annotations

from decimal import Decimal

from atlas_risk.engine import evaluate
from atlas_risk.rules import RULE_ORDER, STAGES
from atlas_risk.verdict import build


def test_there_are_twenty_two_evaluated_rules_in_six_stages() -> None:
    assert len(RULE_ORDER) == 22
    assert len(set(RULE_ORDER)) == 22
    assert len(STAGES) == 6


def test_the_order_matches_the_contract_stage_by_stage() -> None:
    assert STAGES[0] == ("MARKET_NOT_SPOT", "VENUE_NOT_LIVE_ENABLED")
    assert STAGES[3] == ("MIN_ORDER_EXCEEDS_RISK_BUDGET", "POSITION_SIZE_EXCEEDS_MAX")
    assert STAGES[5][-1] == "BUDGET_BLOCKED"
    assert RULE_ORDER[0] == "MARKET_NOT_SPOT"
    assert RULE_ORDER[-1] == "BUDGET_BLOCKED"


def test_a_healthy_book_passes_every_rule_in_the_documented_order(snapshot, trade, config, profile):
    evaluation = evaluate(
        trade=trade,
        snapshot=snapshot,
        config=config,
        profile=profile,
        step_size=Decimal("0.00000001"),
    )
    assert [result.rule_id for result in evaluation.rule_results] == list(RULE_ORDER)
    assert all(result.passed for result in evaluation.rule_results)
    assert evaluation.primary_rule_id is None
    assert evaluation.approved is True


def test_the_fixture_sizes_inside_the_risk_budget(snapshot, trade, config, profile):
    evaluation = evaluate(
        trade=trade,
        snapshot=snapshot,
        config=config,
        profile=profile,
        step_size=Decimal("0.00000001"),
    )
    assert evaluation.sizing is not None
    # 0.5% of 1,000,000 = 5,000 budget over a 50 IDR stop = 100 units.
    assert evaluation.sizing.quantity == Decimal("100")
    assert evaluation.sizing.notional_idr == Decimal("100000")
    # Realised risk is the budget plus the round trip: the gate reports what a stop-out costs.
    assert Decimal("0.5") <= evaluation.sizing.realised_risk_pct <= Decimal("1.0")
    assert evaluation.sizing.realised_risk_idr == Decimal("7173")


def test_the_same_inputs_produce_the_same_digest(snapshot, trade, config, profile):
    first = evaluate(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    second = evaluate(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert first.input_hash == second.input_hash


def test_an_approved_verdict_is_signed_and_expires_in_sixty_seconds(
    snapshot, trade, config, profile, signing_key
):
    from uuid import uuid4

    evaluation = evaluate(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    verdict = build(
        evaluation=evaluation,
        trade=trade,
        snapshot=snapshot,
        config=config,
        profile=profile,
        verdict_at=snapshot.now,
        signing_key=signing_key,
        portfolio_snapshot_id=uuid4(),
        venue_profile_id=1,
    )
    assert verdict.approved is True
    assert verdict.signature is not None
    assert (verdict.expires_at - verdict.verdict_at).total_seconds() == 60
    assert verdict.cost_model_source == "VERIFIED"
