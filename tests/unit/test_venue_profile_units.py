"""Percent-unit guard for every venue fee and tax figure (ADR 0001, FR-053).

The cost model divides by 100, so a value written as a fraction (0.003 for 0.3%) understates
the round trip by a factor of a hundred and quietly makes an unprofitable trade look fine. A
fraction-scale number is therefore a test failure wherever it lives: the adapter constant and
the SQL seed both carry the same numbers, and they must agree.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from atlas_execution.venues.indodax_public import INDOdax_PROFILE

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "infra" / "db" / "seeds" / "0001_venue_profile_indodax.sql"

# The smallest legitimate percent figure here is 0.03 (the USDT taker rate). Anything below it
# that is not exactly zero is a fraction pretending to be a percentage.
SMALLEST_CREDIBLE_PCT = Decimal("0.03")
LARGEST_CREDIBLE_PCT = Decimal("5")

PERCENT_FIELDS = (
    "fee_maker_pct",
    "fee_taker_pct",
    "cfx_fee_pct_assumed",
    "tax_buy_pct_assumed",
    "tax_pph_pct",
    "tax_ppn_pct",
)


def _check(name: str, value: Decimal) -> None:
    assert Decimal(0) <= value <= LARGEST_CREDIBLE_PCT, f"{name}={value} is not a percent"
    assert value == 0 or value >= SMALLEST_CREDIBLE_PCT, (
        f"{name}={value} looks like a fraction; percent units write 0.2 for 0.2%"
    )


def _seed_values() -> list[Decimal]:
    """The six fee/tax figures, in seed order: maker, taker, cfx assumed, buy-tax, PPh, PPN.

    Parsing stops at six because the tuple continues with min_notional and the JSON order rules,
    which are not percentages and would make the guard meaningless.
    """
    text = SEED.read_text()
    block = text.split("VALUES (", 1)[1].split("ON CONFLICT", 1)[0]
    found = [Decimal(m) for m in re.findall(r"^\s+(\d+\.\d+),", block, re.MULTILINE)]
    return found[:6]


def test_the_adapter_declares_every_fee_in_percent_units() -> None:
    for field in PERCENT_FIELDS:
        _check(f"adapter.{field}", getattr(INDOdax_PROFILE, field))


def test_the_sql_seed_declares_every_fee_in_percent_units() -> None:
    values = _seed_values()
    assert len(values) == 6, "the seed no longer parses; the guard would pass vacuously"
    for index, value in enumerate(values):
        _check(f"seed.value[{index}]", value)


def test_the_adapter_and_the_seed_carry_the_same_numbers() -> None:
    values = _seed_values()
    for field in PERCENT_FIELDS:
        value = getattr(INDOdax_PROFILE, field)
        assert value in values, f"{field}={value} does not appear in the seed"


def test_a_fraction_written_as_percent_is_rejected() -> None:
    """The guard's own negative case: 0.003 is what the bug looked like."""
    for bad in (Decimal("0.003"), Decimal("0.0021"), Decimal("0.0001")):
        try:
            _check("synthetic", bad)
        except AssertionError:
            continue
        raise AssertionError(f"{bad} was accepted as a percent")


def test_the_verified_figures_are_pinned_to_adr_0001() -> None:
    """Facts 5, 8 and 9 of ADR 0001. A change here needs a new profile version, not an edit."""
    assert INDOdax_PROFILE.fee_maker_pct == Decimal("0.10000")
    assert INDOdax_PROFILE.fee_taker_pct == Decimal("0.20000")
    assert INDOdax_PROFILE.tax_pph_pct == Decimal("0.21000")
    assert INDOdax_PROFILE.tax_ppn_pct == Decimal("0.00000")
    assert INDOdax_PROFILE.tax_applies_to == "SELL_ONLY"
    assert INDOdax_PROFILE.cfx_fee_pct is None, "CFX stays unverified, never substituted"
