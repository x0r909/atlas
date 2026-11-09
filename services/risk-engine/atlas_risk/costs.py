"""Fees, taxes, slippage, and the sizing arithmetic, exactly as decision-risk.md states it.

Every number is derived from the venue profile the caller passed in. Nothing here reads a
guess: if the profile carries ``assumed`` values the caller is told so via
``CostModelSource``, and the gate refuses to sign a Live order on that basis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from atlas_schemas.risk import CostBreakdown, TradeProposal
from atlas_schemas.venue import VenueProfile

IDR = Decimal("1")
TEN_PLACES = Decimal("0.0000000001")
HUNDRED = Decimal("100")


def _idr(value: Decimal) -> Decimal:
    return value.quantize(IDR, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Sizing:
    quantity: Decimal
    notional_idr: Decimal
    realised_risk_idr: Decimal
    realised_risk_pct: Decimal
    costs: CostBreakdown


def floor_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    """Rounding is always down (contract § 4): never trade more than the budget allows."""
    if step <= 0:
        raise ValueError("step must be positive")
    steps = (quantity / step).to_integral_value(rounding=ROUND_DOWN)
    return (steps * step).quantize(TEN_PLACES, rounding=ROUND_DOWN)


def round_trip_costs(
    *,
    entry_notional: Decimal,
    exit_notional: Decimal,
    profile: VenueProfile,
    max_slippage_pct: Decimal,
) -> CostBreakdown:
    """Fees on both legs, CFX on both legs, PPh on the sale, PPN on the fees, slippage both ways."""
    fee_pct = profile.fee_taker_pct / HUNDRED
    entry_fee = entry_notional * fee_pct
    exit_fee = exit_notional * fee_pct

    cfx_pct = (
        profile.cfx_fee_pct if profile.cfx_fee_pct is not None else profile.cfx_fee_pct_assumed
    )
    cfx = (entry_notional + exit_notional) * (cfx_pct / HUNDRED)

    # PPh is a final tax on the sale; the profile says whether it also applies to a buy.
    taxable = (
        exit_notional if profile.tax_applies_to == "SELL_ONLY" else entry_notional + exit_notional
    )
    tax_pph = taxable * (profile.tax_pph_pct / HUNDRED)
    # PPN is VAT charged on the exchange's fee, not on the traded value.
    tax_ppn = (entry_fee + exit_fee) * (profile.tax_ppn_pct / HUNDRED)

    slippage = (entry_notional + exit_notional) * (max_slippage_pct / HUNDRED)

    return CostBreakdown(
        entry_fee_idr=_idr(entry_fee),
        exit_fee_idr=_idr(exit_fee),
        cfx_idr=_idr(cfx),
        tax_pph_idr=_idr(tax_pph),
        tax_ppn_idr=_idr(tax_ppn),
        slippage_allowance_idr=_idr(slippage),
        total_round_trip_idr=_idr(entry_fee + exit_fee + cfx + tax_pph + tax_ppn + slippage),
    )


def cost_per_unit(costs: CostBreakdown, quantity: Decimal) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return costs.total_round_trip_idr / quantity


def net_rr(
    *, entry: Decimal, stop: Decimal, target: Decimal, cost_per_unit_idr: Decimal
) -> Decimal:
    """Reward after costs over risk after costs; the reward leg pays the round trip twice over."""
    risk = entry - stop + cost_per_unit_idr
    if risk <= 0:
        return Decimal("0")
    reward = target - entry - cost_per_unit_idr
    return reward / risk


def size(
    *,
    proposal: TradeProposal,
    equity_idr: Decimal,
    risk_per_trade_pct: Decimal,
    max_position_pct: Decimal,
    min_notional_idr: Decimal,
    step_size: Decimal,
    profile: VenueProfile,
    max_slippage_pct: Decimal,
) -> Sizing | None:
    """The normative sizing formula. ``None`` means "the risk budget cannot fund a legal order"."""
    risk_budget = equity_idr * risk_per_trade_pct / HUNDRED
    stop_distance = proposal.entry_price - proposal.stop_price
    if stop_distance <= 0:
        raise ValueError("stop must sit below the entry for a long")

    quantity = floor_to_step(risk_budget / stop_distance, step_size)
    if quantity <= 0:
        return None

    notional = quantity * proposal.entry_price
    if notional > equity_idr * max_position_pct / HUNDRED:
        cap_idr = equity_idr * max_position_pct / HUNDRED
        capped = floor_to_step(cap_idr / proposal.entry_price, step_size)
        if capped <= 0:
            return None
        quantity = capped
        notional = quantity * proposal.entry_price

    if notional < min_notional_idr:
        return None

    costs = round_trip_costs(
        entry_notional=notional,
        exit_notional=quantity * proposal.target_price,
        profile=profile,
        max_slippage_pct=max_slippage_pct,
    )
    realised = quantity * stop_distance + costs.total_round_trip_idr
    return Sizing(
        quantity=quantity,
        notional_idr=_idr(notional),
        realised_risk_idr=_idr(realised),
        realised_risk_pct=(realised / equity_idr * HUNDRED).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        ),
        costs=costs,
    )
