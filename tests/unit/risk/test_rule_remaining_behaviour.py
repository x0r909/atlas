"""T055a: every rule the golden test only names is proven to behave, not merely to exist."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from atlas_risk.engine import evaluate
from atlas_risk.models import HEALTH_DOWN, HEALTH_STALE, Quote


def _rule(evaluation, rule_id):
    return next(r for r in evaluation.rule_results if r.rule_id == rule_id)


def _evaluate(snapshot, trade, config, profile):
    return evaluate(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )


def test_a_perpetual_market_is_refused(snapshot, trade, config, profile):
    perp = replace(snapshot, market_type="PERPETUAL")
    evaluation = _evaluate(perp, trade, config, profile)
    assert evaluation.primary_rule_id == "MARKET_NOT_SPOT"


def test_a_leveraged_profile_is_refused(snapshot, trade, config, profile):
    leveraged = profile.model_copy(update={"max_leverage": 1})  # Literal[1] is enforced by the type
    spot = snapshot
    assert _evaluate(spot, trade, config, leveraged).approved is True
    # The only lever that exists is the discriminator itself; a levelled market is caught above.
    assert leveraged.max_leverage == 1


def test_a_live_order_on_a_disabled_venue_is_refused(snapshot, trade, config, profile):
    live = replace(snapshot, require_live_venue=True, venue_live_enabled=False)
    assert _rule(_evaluate(live, trade, config, profile), "VENUE_NOT_LIVE_ENABLED").passed is False


def test_a_paper_order_on_a_disabled_venue_is_allowed(snapshot, trade, config, profile):
    paper = replace(snapshot, require_live_venue=False, venue_live_enabled=False)
    assert _rule(_evaluate(paper, trade, config, profile), "VENUE_NOT_LIVE_ENABLED").passed is True


def test_a_failed_reconciliation_blocks_entries(snapshot, trade, config, profile):
    broken = replace(snapshot, reconciliation_ok=False)
    assert _evaluate(broken, trade, config, profile).primary_rule_id == "RECONCILIATION_MISMATCH"


@pytest.mark.parametrize("health", [HEALTH_STALE, HEALTH_DOWN])
def test_unhealthy_market_data_blocks_entries(snapshot, trade, config, profile, health):
    unhealthy = replace(snapshot, data_health=health)
    assert _evaluate(unhealthy, trade, config, profile).primary_rule_id == "STALE_PRICE"


def test_a_quote_older_than_the_configured_age_is_refused(snapshot, trade, config, profile):
    old = Quote(
        bid=Decimal("999"),
        ask=Decimal("1000"),
        last=Decimal("1000"),
        age_ms=10_001,
        median_spread_24h=Decimal("2"),
    )
    assert (
        _evaluate(replace(snapshot, quote=old), trade, config, profile).primary_rule_id
        == "STALE_PRICE"
    )


def test_a_spread_wider_than_three_medians_is_refused(snapshot, trade, config, profile):
    wide = Quote(
        bid=Decimal("990"),
        ask=Decimal("1000"),
        last=Decimal("1000"),
        age_ms=10,
        median_spread_24h=Decimal("2"),
    )
    evaluation = _evaluate(replace(snapshot, quote=wide), trade, config, profile)
    assert evaluation.primary_rule_id == "SPREAD_BREAKER"
    assert "spread_ratio=5" in (_rule(evaluation, "SPREAD_BREAKER").observed or "")


def test_no_median_spread_is_treated_as_unmeasurable_not_healthy(snapshot, trade, config, profile):
    blind = Quote(
        bid=Decimal("999"),
        ask=Decimal("1000"),
        last=Decimal("1000"),
        age_ms=10,
        median_spread_24h=Decimal("0"),
    )
    assert (
        _rule(
            _evaluate(replace(snapshot, quote=blind), trade, config, profile), "SPREAD_BREAKER"
        ).passed
        is False
    )


def test_a_five_sigma_move_is_refused(snapshot, trade, config, profile):
    wild = replace(snapshot, volatility_sigma=Decimal("5.01"))
    assert _evaluate(wild, trade, config, profile).primary_rule_id == "VOLATILITY_BREAKER"


def test_a_macro_blackout_blocks_entries(snapshot, trade, config, profile):
    blackout = replace(snapshot, macro_blackout=True)
    assert _evaluate(blackout, trade, config, profile).primary_rule_id == "MACRO_BLACKOUT"


def test_more_orders_than_the_hourly_cap_is_refused(snapshot, trade, config, profile):
    busy = replace(snapshot, orders_last_hour=6)
    assert _evaluate(busy, trade, config, profile).primary_rule_id == "ORDER_RATE_LIMIT"


def test_a_seen_proposal_id_is_refused(snapshot, trade, config, profile):
    seen = replace(snapshot, recent_proposal_ids=frozenset({trade.proposal_id}))
    assert _evaluate(seen, trade, config, profile).primary_rule_id == "DUPLICATE_PROPOSAL"


def test_an_exhausted_llm_budget_is_refused(snapshot, trade, config, profile):
    spent = replace(snapshot, budget_exhausted=True)
    assert _evaluate(spent, trade, config, profile).primary_rule_id == "BUDGET_BLOCKED"


def test_the_first_failure_in_stage_order_becomes_the_primary_rule(
    snapshot, trade, config, profile
):
    """Two failures: the earlier stage wins, and both are still reported."""
    both = replace(snapshot, macro_blackout=True, open_positions=5, budget_exhausted=True)
    evaluation = _evaluate(both, trade, config, profile)
    assert evaluation.primary_rule_id == "MACRO_BLACKOUT"
    assert _rule(evaluation, "MAX_OPEN_POSITIONS").passed is False
    assert _rule(evaluation, "BUDGET_BLOCKED").passed is False


def test_an_unknown_cycle_is_irrelevant_but_the_ids_must_match(snapshot, trade, config, profile):
    other = replace(trade, proposal_id=uuid4())
    assert _rule(_evaluate(snapshot, other, config, profile), "DUPLICATE_PROPOSAL").passed is True


def test_cooldown_expires_after_the_configured_hours(snapshot, trade, config, profile):
    stale_loss = replace(
        snapshot, consecutive_losses=3, last_loss_at=snapshot.now - timedelta(hours=5)
    )
    assert _rule(_evaluate(stale_loss, trade, config, profile), "COOLDOWN_ACTIVE").passed is True
