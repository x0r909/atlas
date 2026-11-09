"""T053 / SC-010: net R:R must be computed after every cost the venue actually charges."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from atlas_risk import costs
from atlas_risk.engine import evaluate


def _result(evaluation, rule_id):
    return next(r for r in evaluation.rule_results if r.rule_id == rule_id)


def test_a_thin_target_is_rejected_once_costs_are_in(snapshot, trade, config, profile):
    thin = replace(trade, target_price=Decimal("1030"))
    evaluation = evaluate(
        trade=thin, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )

    assert evaluation.approved is False
    assert evaluation.primary_rule_id == "RR_BELOW_MIN"
    failed = _result(evaluation, "RR_BELOW_MIN")
    assert failed.observed is not None and "rr_net=" in failed.observed


def test_the_same_trade_without_costs_would_look_acceptable(snapshot, trade, config, profile):
    thin = replace(trade, target_price=Decimal("1030"))
    # Gross reward/risk is 30/50 = 0.6, still below 1.5: the point is that costs only worsen it.
    gross = (thin.target_price - thin.entry_price) / (thin.entry_price - (thin.stop_price or 0))
    evaluation = evaluate(
        trade=thin, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert evaluation.rr_net is not None and evaluation.rr_net < gross


def test_each_cost_component_moves_the_ratio(snapshot, trade, config, profile):
    base = costs.round_trip_costs(
        entry_notional=trade.entry_price,
        exit_notional=trade.target_price or Decimal("0"),
        profile=profile,
        max_slippage_pct=config.max_slippage_pct,
    )
    pricier = costs.round_trip_costs(
        entry_notional=trade.entry_price,
        exit_notional=trade.target_price or Decimal("0"),
        profile=profile.model_copy(
            update={"fee_taker_pct": Decimal("0.600"), "cfx_fee_pct": Decimal("0.500")}
        ),
        max_slippage_pct=config.max_slippage_pct,
    )
    assert pricier.total_round_trip_idr > base.total_round_trip_idr
    assert pricier.cfx_idr > base.cfx_idr
    assert pricier.entry_fee_idr == base.entry_fee_idr * 2


def test_the_exit_leg_is_included_in_the_round_trip(snapshot, trade, config, profile):
    """A higher target pays more exit fee, PPh and slippage: the reward leg is not free."""
    near = costs.round_trip_costs(
        entry_notional=Decimal("1000"),
        exit_notional=Decimal("1100"),
        profile=profile,
        max_slippage_pct=config.max_slippage_pct,
    )
    far = costs.round_trip_costs(
        entry_notional=Decimal("1000"),
        exit_notional=Decimal("2000"),
        profile=profile,
        max_slippage_pct=config.max_slippage_pct,
    )
    assert far.tax_pph_idr > near.tax_pph_idr
    assert far.total_round_trip_idr > near.total_round_trip_idr


def test_a_wide_target_passes_the_ratio_rule(snapshot, trade, config, profile):
    fat = replace(trade, target_price=Decimal("1250"))
    evaluation = evaluate(
        trade=fat, snapshot=snapshot, config=config, profile=profile, step_size=Decimal("1")
    )
    assert _result(evaluation, "RR_BELOW_MIN").passed is True
