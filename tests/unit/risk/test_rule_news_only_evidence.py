"""T053a / FR-013: tier-3 chatter may support a thesis, never be its only justification."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from atlas_risk.engine import evaluate


def _rule(evaluation):
    return next(r for r in evaluation.rule_results if r.rule_id == "NEWS_ONLY_EVIDENCE")


def test_only_tier_three_material_is_rejected(snapshot, trade, config, profile):
    chatter = replace(trade, evidence_kinds=("NEWS", "SOCIAL"), evidence_tiers=(3, 3))
    evaluation = evaluate(
        trade=chatter, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )

    assert evaluation.primary_rule_id == "NEWS_ONLY_EVIDENCE"
    failed = _rule(evaluation)
    assert failed.passed is False
    assert failed.observed is not None and "tiers=[3]" in failed.observed


def test_one_verified_source_beside_the_chatter_is_enough(snapshot, trade, config, profile):
    mixed = replace(trade, evidence_kinds=("BAR", "NEWS"), evidence_tiers=(None, 3))
    evaluation = evaluate(
        trade=mixed, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation).passed is True


def test_a_tier_one_headline_alone_passes(snapshot, trade, config, profile):
    official = replace(trade, evidence_kinds=("NEWS",), evidence_tiers=(1,))
    evaluation = evaluate(
        trade=official, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation).passed is True


def test_an_empty_evidence_list_is_rejected(snapshot, trade, config, profile):
    bare = replace(trade, evidence_kinds=(), evidence_tiers=())
    evaluation = evaluate(
        trade=bare, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation).passed is False
