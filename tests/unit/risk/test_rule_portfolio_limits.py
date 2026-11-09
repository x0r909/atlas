"""T055: the book's own state is a rule input, not a post-hoc check."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from atlas_risk.engine import evaluate


def _rule(evaluation, rule_id):
    return next(r for r in evaluation.rule_results if r.rule_id == rule_id)


def test_asset_exposure_over_half_the_book_is_rejected(snapshot, trade, config, profile):
    loaded = replace(snapshot, asset_exposure_idr=Decimal("600000"))
    evaluation = evaluate(
        trade=trade, snapshot=loaded, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.primary_rule_id == "ASSET_EXPOSURE_EXCEEDS_MAX"


def test_total_exposure_over_one_times_equity_is_rejected(snapshot, trade, config, profile):
    loaded = replace(snapshot, total_exposure_idr=Decimal("1100000"))
    evaluation = evaluate(
        trade=trade, snapshot=loaded, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation, "TOTAL_EXPOSURE_EXCEEDS_MAX").passed is False


def test_daily_loss_at_the_limit_is_rejected(snapshot, trade, config, profile):
    down = replace(snapshot, equity_idr=Decimal("980000"))
    evaluation = evaluate(
        trade=trade, snapshot=down, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.primary_rule_id == "DAILY_LOSS_LIMIT"


def test_drawdown_past_ten_percent_is_rejected(snapshot, trade, config, profile):
    down = replace(snapshot, equity_idr=Decimal("895000"))
    evaluation = evaluate(
        trade=trade, snapshot=down, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation, "MAX_DRAWDOWN").passed is False


def test_a_full_slate_of_positions_is_rejected(snapshot, trade, config, profile):
    full = replace(snapshot, open_positions=5)
    evaluation = evaluate(
        trade=trade, snapshot=full, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.primary_rule_id == "MAX_OPEN_POSITIONS"


def test_a_drawdown_that_is_large_but_below_the_limit_passes(snapshot, trade, config, profile):
    down = replace(snapshot, equity_idr=Decimal("950000"))
    evaluation = evaluate(
        trade=trade, snapshot=down, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation, "MAX_DRAWDOWN").passed is True
    assert _rule(evaluation, "DAILY_LOSS_LIMIT").passed is False  # 5% > 2% daily


def test_a_loss_streak_inside_the_cooldown_window_blocks_new_entries(
    snapshot, trade, config, profile
):
    stormy = replace(
        snapshot,
        consecutive_losses=3,
        last_loss_at=snapshot.now - timedelta(hours=1),
    )
    evaluation = evaluate(
        trade=trade, snapshot=stormy, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _rule(evaluation, "COOLDOWN_ACTIVE").passed is False
