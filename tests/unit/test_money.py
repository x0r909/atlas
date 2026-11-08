"""Money arithmetic (T011).

Two properties are the whole point:

* ``floor_to_step`` NEVER rounds up — an order larger than the risk engine sized is a
  risk-limit breach, and the venue would accept it.
* A ``float`` never enters the money path — ``0.1 + 0.2`` is a bug generator in code that
  decides position size.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_common.money import (
    MoneyError,
    dec,
    fee,
    floor_to_step,
    meets_min_notional,
    quantize_idr,
    round_trip_cost,
    tax_on_sell,
    tick_round,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- exactness ---------------------------------------------------------------------------


def test_float_is_refused() -> None:
    with pytest.raises(MoneyError, match="float"):
        dec(0.1)  # type: ignore[arg-type]  # deliberately wrong type


def test_bool_is_refused() -> None:
    with pytest.raises(MoneyError):
        dec(True)


def test_string_and_int_are_accepted() -> None:
    assert dec("10000") == Decimal(10000)
    assert dec(10000) == Decimal(10000)
    assert dec("0.00000001") == Decimal("1E-8")


def test_non_finite_is_refused() -> None:
    with pytest.raises(MoneyError, match="finite"):
        dec("NaN")


def test_quantize_idr_rounds_half_up() -> None:
    assert quantize_idr("10000.5") == Decimal("10001")
    assert quantize_idr("10000.4") == Decimal("10000")


# --- floor_to_step -----------------------------------------------------------------------


def test_floor_to_step_floors() -> None:
    assert floor_to_step("0.000123456789", "0.00000001") == Decimal("0.00012345")


def test_floor_to_step_below_one_step_is_zero() -> None:
    # Not "the minimum" — the caller must treat 0 as "cannot trade".
    assert floor_to_step("0.000000001", "0.00000001") == 0


def test_floor_to_step_rejects_zero_step() -> None:
    with pytest.raises(MoneyError, match="step"):
        floor_to_step("1", "0")


def test_floor_to_step_rejects_negative_quantity() -> None:
    with pytest.raises(MoneyError, match="negative"):
        floor_to_step("-1", "0.1")


@given(
    quantity=st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"), places=12),
    step=st.decimals(min_value=Decimal("0.00000001"), max_value=Decimal("1000"), places=8),
)
@settings(max_examples=300, deadline=None)
def test_floor_to_step_never_rounds_up(quantity: Decimal, step: Decimal) -> None:
    result = floor_to_step(quantity, step)
    assert result <= quantity, f"floor_to_step rounded UP: {quantity} / {step} -> {result}"
    assert result >= 0
    # Result is a whole multiple of the step, and one more step would exceed the input.
    assert result % step == 0, f"{result} is not a multiple of {step}"
    assert result + step > quantity


# --- tick_round --------------------------------------------------------------------------


def test_tick_round_directions() -> None:
    assert tick_round("1000050", "1000", "down") == Decimal("1000000")
    assert tick_round("1000050", "1000", "up") == Decimal("1001000")
    assert tick_round("1000500", "1000", "nearest") == Decimal("1001000")


def test_tick_round_up_when_already_aligned() -> None:
    assert tick_round("1001000", "1000", "up") == Decimal("1001000")


def test_tick_round_rejects_unknown_direction() -> None:
    with pytest.raises(MoneyError, match="direction"):
        tick_round("1", "1", "sideways")


# --- fees and taxes ----------------------------------------------------------------------


def test_taker_fee_on_100000_at_0_2_percent() -> None:
    assert fee("100000", "0.2") == Decimal("200.000")


def test_fee_rejects_negative_percent() -> None:
    with pytest.raises(MoneyError):
        fee("100000", "-0.1")


def test_pph_on_sell_is_0_21_percent() -> None:
    assert tax_on_sell("100000", "0.21") == Decimal("210.000")


def test_round_trip_uses_taker_on_both_legs_and_pph_on_the_sell_only() -> None:
    cost = round_trip_cost(
        "100000", "100000", taker_percent="0.2", pph_percent="0.21", ppn_percent=0
    )
    # 200 entry taker + 200 exit taker + 210 PPh on the sell = 610
    assert cost == Decimal("610.000")


def test_round_trip_includes_cfx_on_both_legs_when_supplied() -> None:
    with_cfx = round_trip_cost(
        "100000", "100000", taker_percent="0.2", pph_percent="0.21", cfx_percent="0.1"
    )
    without_cfx = round_trip_cost("100000", "100000", taker_percent="0.2", pph_percent="0.21")
    assert with_cfx - without_cfx == Decimal("200.000")


def test_round_trip_without_cfx_does_not_silently_substitute_zero_for_it() -> None:
    """The honest signal is the number the caller asked for; the *policy* lives upstream.

    ``cfx_percent=None`` must leave the CFX term out entirely (so the result is provably
    smaller), because only the Risk Engine may decide that an assumed rate is acceptable
    and label the verdict ``ASSUMED``.
    """
    cost = round_trip_cost("100000", "100000", taker_percent="0.2", pph_percent="0.21")
    assert cost == Decimal("610.000")
    assert cost != round_trip_cost(
        "100000", "100000", taker_percent="0.2", pph_percent="0.21", cfx_percent="0.1"
    )


# --- venue minimums ----------------------------------------------------------------------


def test_min_notional_is_inclusive() -> None:
    assert meets_min_notional("10000", "10000")
    assert not meets_min_notional("9999", "10000")
