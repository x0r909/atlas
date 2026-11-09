"""The simulator is the only venue M3 may execute against, so it gets its own test."""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_schemas.venue import (
    OrderBook,
    PlaceOrderRequest,
    Ticker,
    VenueRejected,
    VenueUnavailable,
)
from atlas_schemas.venue_port import VenuePort, assert_port_surface

from tests.support.simulated_venue import SimulatedVenue


async def test_the_simulator_is_a_complete_venue_port() -> None:
    venue = SimulatedVenue()
    assert isinstance(venue, VenuePort) or hasattr(venue, "place_order")
    assert_port_surface(SimulatedVenue)
    assert venue.capabilities().markets == ["SPOT"]
    assert venue.capabilities().max_leverage == 1


async def test_a_limit_order_acknowledges_and_is_recorded_once() -> None:
    venue = SimulatedVenue(price=Decimal("1000"))
    request = PlaceOrderRequest(
        pair="BTCIDR",
        type="LIMIT",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("1000"),
        client_order_id="atlas-c-p-1",
        signed_order_id="so-1",
    )
    ack = await venue.place_order(request)
    assert ack.status == "FILLED"
    assert [order.request.client_order_id for order in venue.orders] == ["atlas-c-p-1"]
    assert venue.place_order_calls == 1


async def test_a_partial_fill_reports_the_filled_quantity() -> None:
    venue = SimulatedVenue(price=Decimal("1000"), fill_fraction=Decimal("0.4"))
    request = PlaceOrderRequest(
        pair="BTCIDR",
        type="LIMIT",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("1000"),
        client_order_id="atlas-c-p-2",
        signed_order_id="so-2",
    )
    ack = await venue.place_order(request)
    assert ack.status == "PARTIALLY_FILLED"
    assert ack.venue_order_id is not None
    order = await venue.get_order("BTCIDR", ack.venue_order_id)
    assert order is not None
    assert order.filled_quantity == Decimal("0.004")
    assert len(await venue.my_trades("BTCIDR")) == 1


async def test_a_programmed_rejection_still_records_the_request() -> None:
    """The 'zero venue requests' assertions elsewhere only mean something if a request
    that *was* sent always shows up in ``orders``."""
    venue = SimulatedVenue()
    venue.reject_next("INSUFFICIENT_BALANCE", "not enough IDR")
    request = PlaceOrderRequest(
        pair="BTCIDR",
        type="LIMIT",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("1000"),
        client_order_id="atlas-c-p-3",
        signed_order_id="so-3",
    )
    with pytest.raises(VenueRejected, match="not enough IDR"):
        await venue.place_order(request)
    assert venue.place_order_calls == 1
    assert len(venue.orders) == 1


async def test_a_programmed_timeout_looks_like_an_unknown_outcome() -> None:
    venue = SimulatedVenue()
    venue.timeout_next()
    request = PlaceOrderRequest(
        pair="BTCIDR",
        type="LIMIT",
        side="BUY",
        quantity=Decimal("0.01"),
        limit_price=Decimal("1000"),
        client_order_id="atlas-c-p-4",
        signed_order_id="so-4",
    )
    with pytest.raises(VenueUnavailable):
        await venue.place_order(request)
    assert venue.place_order_calls == 1


async def test_cancel_and_get_order_agree() -> None:
    venue = SimulatedVenue()
    ack = await venue.place_order(
        PlaceOrderRequest(
            pair="BTCIDR",
            type="LIMIT",
            side="BUY",
            quantity=Decimal("0.01"),
            limit_price=Decimal("900"),
            client_order_id="atlas-c-p-5",
            signed_order_id="so-5",
        )
    )
    assert ack.venue_order_id is not None
    await venue.cancel_order("BTCIDR", ack.venue_order_id)
    order = await venue.get_order("BTCIDR", ack.venue_order_id)
    assert order is not None
    assert order.status == "CANCELED"
    assert await venue.open_orders("BTCIDR") == []


async def test_the_book_and_ticker_follow_the_last_price() -> None:
    venue = SimulatedVenue(price=Decimal("1000"))
    ticker = await venue.tickers(["BTCIDR"])
    assert ticker[0].last == Decimal("1000")
    book = await venue.order_book("BTCIDR")
    assert isinstance(book, OrderBook)
    assert book.bids[0][0] < ticker[0].last < book.asks[0][0]
    assert isinstance(ticker[0], Ticker)
