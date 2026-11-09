"""What an order will actually cost: walk the book, do not look at the top level.

FR-032's pre-trade guard and FR-058's cost model both need this, and neither may use floating
point: a slippage estimate that drifts by a hundredth of a percent is a rule that fires or
does not fire for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Prices are quoted to 8 dp by the venue; a VWAP is an average, so it keeps 10.
VWAP_EXPONENT = Decimal("0.0000000001")

Level = tuple[Decimal, Decimal]  # (price, size)
Side = Literal["BUY", "SELL"]


class InsufficientDepth(ValueError):
    """The book did not have enough size to fill the order."""

    def __init__(self, filled_quantity: Decimal, remaining_quantity: Decimal) -> None:
        super().__init__(
            f"book filled {filled_quantity} of the requested quantity; "
            f"{remaining_quantity} left unfilled"
        )
        self.filled_quantity = filled_quantity
        self.remaining_quantity = remaining_quantity


@dataclass(frozen=True, slots=True)
class BookWalk:
    """A fill the book could serve in full; a shortfall raises instead of half-pricing."""

    filled_quantity: Decimal
    notional: Decimal
    vwap: Decimal


def _check_levels(levels: Sequence[Level]) -> None:
    for price, size in levels:
        if price <= 0:
            raise ValueError("price must be positive")
        if size < 0:
            raise ValueError("size must not be negative")


def walk_book(levels: Sequence[Level], quantity: Decimal) -> BookWalk:
    """Consume ``levels`` in the order given until ``quantity`` is filled."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    _check_levels(levels)

    remaining = quantity
    notional = Decimal(0)
    filled = Decimal(0)
    for price, size in levels:
        if remaining == 0:
            break
        if size == 0:
            continue
        take = min(size, remaining)
        notional += price * take
        filled += take
        remaining -= take

    if remaining > 0:
        # A partially fillable order would make every downstream cost a fiction, so the walk
        # refuses rather than pricing 1 of the 2 requested units.
        raise InsufficientDepth(filled, remaining)
    return BookWalk(
        filled_quantity=filled,
        notional=notional,
        vwap=(notional / filled).quantize(VWAP_EXPONENT),
    )


def expected_price(levels: Sequence[Level], quantity: Decimal, *, side: Side) -> Decimal:
    """The VWAP of filling ``quantity`` against ``levels`` (asks for BUY, bids for SELL)."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"unknown side {side!r}")
    return walk_book(levels, quantity).vwap


def deviation_pct(price: Decimal, reference: Decimal) -> Decimal:
    """How far ``price`` is from ``reference``, in percent, signed."""
    if reference <= 0:
        raise ValueError("reference price must be positive")
    return (price / reference - 1) * 100
