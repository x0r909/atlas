"""The order-book walk is the only thing that knows what a trade will actually cost."""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_common.slippage import (
    InsufficientDepth,
    deviation_pct,
    expected_price,
    walk_book,
)

D = Decimal


def test_the_walk_reports_the_vwap_the_notional_and_what_did_not_fit() -> None:
    levels = [(D("1000"), D("1")), (D("1001"), D("1"))]
    walk = walk_book(levels, D("1.5"))
    assert walk.filled_quantity == D("1.5")
    assert walk.notional == D("1500.5")
    assert walk.vwap == D("1000.3333333333")


def test_a_walk_that_cannot_be_filled_says_how_much_it_got() -> None:
    with pytest.raises(InsufficientDepth) as excinfo:
        walk_book([(D("1000"), D("1"))], D("2"))
    assert excinfo.value.filled_quantity == D("1")
    assert excinfo.value.remaining_quantity == D("1")


def test_an_empty_book_is_not_a_zero_price() -> None:
    with pytest.raises(InsufficientDepth):
        walk_book([], D("1"))


def test_buying_walks_asks_upward_and_selling_walks_bids_downward() -> None:
    asks = [(D("1000"), D("1")), (D("1002"), D("1"))]
    bids = [(D("1000"), D("1")), (D("998"), D("1"))]
    assert expected_price(asks, D("1.5"), side="BUY") > D("1000")
    assert expected_price(bids, D("1.5"), side="SELL") < D("1000")


def test_the_side_decides_which_half_of_the_book_is_used() -> None:
    book_asks = [(D("1005"), D("1"))]
    book_bids = [(D("995"), D("1"))]
    assert expected_price(book_asks, D("1"), side="BUY") == D("1005")
    assert expected_price(book_bids, D("1"), side="SELL") == D("995")


def test_deviation_is_measured_against_the_reference_price_and_signed() -> None:
    assert deviation_pct(D("1001"), D("1000")) == D("0.1")
    assert deviation_pct(D("999"), D("1000")) == D("-0.1")


def test_deviation_of_a_zero_reference_is_refused() -> None:
    with pytest.raises(ValueError, match="reference price"):
        deviation_pct(D("1000"), D("0"))


def test_a_quantity_that_is_not_positive_is_refused() -> None:
    with pytest.raises(ValueError, match="quantity"):
        walk_book([(D("1000"), D("1"))], D("0"))
    with pytest.raises(ValueError, match="quantity"):
        walk_book([(D("1000"), D("1"))], D("-1"))


def test_a_level_with_no_size_is_skipped_rather_than_filling_nothing() -> None:
    walk = walk_book([(D("999"), D("0")), (D("1000"), D("1"))], D("1"))
    assert walk.vwap == D("1000")


def test_the_walk_is_exact_arithmetic_not_floating_point() -> None:
    walk = walk_book([(D("0.1"), D("3"))], D("3"))
    assert walk.notional == D("0.3")
    assert walk.vwap == D("0.1")
