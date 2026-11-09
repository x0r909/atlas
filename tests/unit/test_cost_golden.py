"""The cost model against the verified numbers, with nothing borrowed from a fixture.

ADR 0001 facts 5 and 8: IDR taker 0.20%, PPh 0.21% on the seller, PPN 0% since 2025-08-01.
A round trip is therefore taker buy + taker sell + PPh sell = 0.61%, and a Rp10 000 position
pays Rp61. If this test ever disagrees with those figures, the decision engine is pricing
trades on an assumption it did not verify.
"""

from __future__ import annotations

from decimal import Decimal

from atlas_risk.costs import HUNDRED, round_trip_costs
from atlas_schemas.venue import VenueProfile

# CFX is held at a verified zero here so the arithmetic isolates the three components ADR 0001
# prices; the real profile carries cfx_fee_pct = NULL, which makes Live refuse to sign.
VERIFIED = VenueProfile(
    venue_id="indodax",
    version=1,
    fee_maker_pct=Decimal("0.10000"),
    fee_taker_pct=Decimal("0.20000"),
    cfx_fee_pct=Decimal("0.00000"),
    cfx_fee_pct_assumed=Decimal("0.10000"),
    tax_buy_pct_assumed=Decimal("0.21000"),
    tax_pph_pct=Decimal("0.21000"),
    tax_ppn_pct=Decimal("0.00000"),
    tax_applies_to="SELL_ONLY",
    min_notional_idr=Decimal("10000"),
    max_leverage=1,
    native_stop_orders=False,
    cost_model_version="1",
)


def test_the_round_trip_costs_exactly_61_basis_points() -> None:
    costs = round_trip_costs(
        entry_notional=Decimal("10000"),
        exit_notional=Decimal("10000"),
        profile=VERIFIED,
        max_slippage_pct=Decimal("0"),
    )
    total_pct = costs.total_round_trip_idr / Decimal("10000") * HUNDRED
    assert total_pct == Decimal("0.61"), f"round trip is {total_pct}%, ADR 0001 says 0.61%"


def test_a_rp10000_position_pays_rp61() -> None:
    costs = round_trip_costs(
        entry_notional=Decimal("10000"),
        exit_notional=Decimal("10000"),
        profile=VERIFIED,
        max_slippage_pct=Decimal("0"),
    )
    assert costs.total_round_trip_idr == Decimal("61")
    assert costs.entry_fee_idr == Decimal("20")
    assert costs.exit_fee_idr == Decimal("20")
    assert costs.tax_pph_idr == Decimal("21")
    assert costs.tax_ppn_idr == Decimal("0")


def test_the_tax_is_charged_on_the_sale_only() -> None:
    costs = round_trip_costs(
        entry_notional=Decimal("10000"),
        exit_notional=Decimal("10000"),
        profile=VERIFIED,
        max_slippage_pct=Decimal("0"),
    )
    assert costs.tax_pph_idr == Decimal("21")
    doubled = VERIFIED.model_copy(update={"tax_applies_to": "BOTH_SIDES"})
    both = round_trip_costs(
        entry_notional=Decimal("10000"),
        exit_notional=Decimal("10000"),
        profile=doubled,
        max_slippage_pct=Decimal("0"),
    )
    assert both.total_round_trip_idr > costs.total_round_trip_idr


def test_the_cost_scales_with_notional_and_nothing_else() -> None:
    """Twice the position, twice the fee: a percentage that drifts is a bug, not a rounding."""
    doubled = round_trip_costs(
        entry_notional=Decimal("20000"),
        exit_notional=Decimal("20000"),
        profile=VERIFIED,
        max_slippage_pct=Decimal("0"),
    )
    assert doubled.total_round_trip_idr == Decimal("122")
    assert doubled.total_round_trip_idr == Decimal("61") * 2
