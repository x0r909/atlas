"""Indodax public adapter against recorded payloads (T032).

Every assertion pins a mapping decision: what a venue field becomes in our types, and which
of our exceptions a failure turns into. A silently different mapping here is a bad fill later.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from atlas_execution.venues.indodax_public import REST_BASE, IndodaxPublicVenue
from atlas_schemas.venue import (
    PlaceOrderRequest,
    VenueAuthError,
    VenueNotFound,
    VenueRateLimited,
    VenueSchemaError,
    VenueUnavailable,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "indodax"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=REST_BASE)


@pytest.fixture()
def venue(client: httpx.AsyncClient) -> IndodaxPublicVenue:
    return IndodaxPublicVenue(client=client)


async def test_pairs_carry_the_minimum_notional_the_risk_engine_needs(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/pairs").mock(return_value=httpx.Response(200, json=fixture("pairs.json")))
        pairs = {pair.pair: pair for pair in await venue.pairs()}

    btc = pairs["btc_idr"]
    assert btc.min_notional == Decimal("10000")
    assert btc.tick_size == Decimal("1")
    assert btc.step_size == Decimal("0.00000001")
    assert btc.min_qty == Decimal("0.0001")
    assert btc.base == "btc" and btc.quote == "idr"
    assert btc.enabled is True


async def test_an_unknown_pair_is_not_found_not_silently_empty(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/pairs").mock(return_value=httpx.Response(200, json=fixture("pairs.json")))
        with pytest.raises(VenueNotFound):
            await venue.pair("doge_idr")


async def test_a_truncated_book_is_a_schema_error_not_a_thin_book(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/depth/btc_idr").mock(
            return_value=httpx.Response(200, json=fixture("depth_truncated.json"))
        )
        with pytest.raises(VenueSchemaError, match="truncated"):
            await venue.order_book("btc_idr")


async def test_a_healthy_book_keeps_decimal_precision_and_order(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/depth/btc_idr").mock(
            return_value=httpx.Response(200, json=fixture("depth_btc_idr.json"))
        )
        book = await venue.order_book("btc_idr")

    assert book.bids[0] == (Decimal("1035000000"), Decimal("0.02310000"))
    assert book.asks[0][0] > book.bids[0][0]
    assert len(book.bids) == 3


async def test_a_crossed_book_from_the_venue_is_never_returned(venue):
    crossed = {"buy": [["1036000000", "1"]], "sell": [["1035000000", "1"]]}
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/depth/btc_idr").mock(return_value=httpx.Response(200, json=crossed))
        with pytest.raises(VenueSchemaError, match="crossed"):
            await venue.order_book("btc_idr")


async def test_rate_limiting_surfaces_the_retry_hint(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/ticker_all").mock(
            return_value=httpx.Response(429, headers={"retry-after": "7"})
        )
        with pytest.raises(VenueRateLimited) as excinfo:
            await venue.tickers()
    assert excinfo.value.retry_after_s == 7.0


async def test_a_server_error_is_unavailable_so_the_loop_can_back_off(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/ticker_all").mock(return_value=httpx.Response(503))
        with pytest.raises(VenueUnavailable):
            await venue.tickers()


async def test_a_transport_failure_is_unavailable_not_a_crash(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/pairs").mock(side_effect=httpx.ConnectTimeout("timed out"))
        with pytest.raises(VenueUnavailable):
            await venue.pairs()


async def test_a_non_json_body_is_a_schema_error(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/ticker_all").mock(return_value=httpx.Response(200, text="<html>maintenance"))
        with pytest.raises(VenueSchemaError):
            await venue.tickers()


async def test_tickers_keep_only_the_requested_pairs_and_both_sides(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/ticker_all").mock(
            return_value=httpx.Response(200, json=fixture("ticker_all.json"))
        )
        tickers = await venue.tickers(["btc_idr"])

    assert set(tickers) == {"btc_idr"}
    ticker = tickers["btc_idr"]
    assert ticker.bid == Decimal("1035000000")
    assert ticker.ask == Decimal("1035500000")
    assert ticker.bid < ticker.ask
    assert ticker.venue_ts == ticker.ingested_at


async def test_trades_convert_venue_seconds_to_milliseconds(venue):
    with respx.mock(base_url=REST_BASE) as mock:
        mock.get("/api/trades/btc_idr").mock(
            return_value=httpx.Response(200, json=fixture("trades_btc_idr.json"))
        )
        trades = await venue.recent_trades("btc_idr")

    assert trades[0].venue_ts == 1_718_000_000_000
    assert trades[0].side == "BUY"
    assert trades[1].side == "SELL"
    assert trades[0].trade_id == "71234501"


async def test_bars_expand_parallel_series_into_rows(venue):
    with respx.mock() as mock:
        mock.get(
            "https://indodax.com/tradingview/history",
            params={
                "symbol": "BTCIDR",
                "resolution": "60",
                "from": "1717996800",
                "to": "1717996920",
            },
        ).mock(return_value=httpx.Response(200, json=fixture("history_btc_idr.json")))
        bars = await venue.bars("btc_idr", "1h", 1_717_996_800_000, 1_717_996_920_000)

    assert len(bars) == 3
    assert bars[0].ts == 1_717_996_800_000
    assert bars[0].open == Decimal("1034000000")
    assert bars[2].close == Decimal("1035250000")
    assert bars[1].timeframe == "1h"


async def test_no_data_is_an_empty_series_not_an_error(venue):
    with respx.mock() as mock:
        mock.get("https://indodax.com/tradingview/history").mock(
            return_value=httpx.Response(200, json={"s": "no_data"})
        )
        assert await venue.bars("btc_idr", "1h", 0, 1000) == []


async def test_ragged_history_is_refused(venue):
    ragged = {"s": "ok", "t": [1, 2], "o": [1], "h": [1, 2], "l": [1, 2], "c": [1, 2], "v": [1, 2]}
    with respx.mock() as mock:
        mock.get("https://indodax.com/tradingview/history").mock(
            return_value=httpx.Response(200, json=ragged)
        )
        with pytest.raises(VenueSchemaError):
            await venue.bars("btc_idr", "1h", 0, 1000)


async def test_an_unsupported_timeframe_never_reaches_the_venue(venue):
    with pytest.raises(VenueSchemaError):
        await venue.bars("btc_idr", "3s", 0, 1000)


@pytest.mark.parametrize(
    "call",
    [
        lambda venue: venue.balances(),
        lambda venue: venue.open_orders(),
        lambda venue: venue.get_order("1", "btc_idr"),
        lambda venue: venue.my_trades("btc_idr"),
        lambda venue: venue.cancel_order("1", "btc_idr"),
        lambda venue: venue.place_order(
            PlaceOrderRequest(
                pair="btc_idr",
                side="BUY",
                type="MARKET",
                quantity=Decimal("0.001"),
                client_order_id="atlas-abc",
                signed_order_id="verdict-1",
            )
        ),
    ],
)
async def test_private_calls_refuse_instead_of_pretending(venue, call):
    with pytest.raises(VenueAuthError):
        await call(venue)
