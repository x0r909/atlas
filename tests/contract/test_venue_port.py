"""VenuePort conformance (T030): what every adapter must and must not expose.

These tests are about the port, not about Indodax. They run against the protocol and a
minimal adapter so a future venue cannot ship a withdraw method behind a friendly name.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from atlas_execution.venues.indodax_public import INDOdax_PROFILE, IndodaxPublicVenue
from atlas_schemas.venue import (
    OrderBook,
    PlaceOrderRequest,
    Ticker,
    VenueAuthError,
    VenueCapabilities,
    VenueError,
    VenueNotFound,
    VenueProfile,
    VenueRateLimited,
    VenueRejected,
    VenueSchemaError,
    VenueUnavailable,
)
from atlas_schemas.venue_port import FORBIDDEN_METHODS, VenuePort, assert_port_surface
from pydantic import ValidationError

PORT_METHODS = {
    "pairs",
    "balances",
    "tickers",
    "order_book",
    "recent_trades",
    "bars",
    "place_order",
    "cancel_order",
    "get_order",
    "open_orders",
    "my_trades",
    "stream_market",
}


def test_the_port_declares_the_expected_surface():
    declared = {name for name in dir(VenuePort) if not name.startswith("_")}
    assert declared >= PORT_METHODS


def test_no_adapter_may_expose_a_withdrawal_surface():
    for name in dir(IndodaxPublicVenue):
        lowered = name.lower()
        assert not any(forbidden in lowered for forbidden in FORBIDDEN_METHODS), name


def test_assert_port_surface_rejects_a_withdraw_method():
    class Sneaky:
        def withdraw(self) -> None: ...

    with pytest.raises(AssertionError, match="forbidden"):
        assert_port_surface(Sneaky)  # type: ignore[arg-type]


def test_every_adapter_error_is_one_of_the_mapped_types():
    for error in (
        VenueUnavailable,
        VenueRateLimited,
        VenueAuthError,
        VenueRejected,
        VenueNotFound,
        VenueSchemaError,
    ):
        assert issubclass(error, VenueError)


def test_rate_limited_carries_the_venue_retry_hint():
    error = VenueRateLimited("slow down", retry_after_s=2.5)
    assert error.retry_after_s == 2.5
    assert VenueRateLimited("slow down").retry_after_s is None


def test_rejected_keeps_the_venue_code_for_the_audit_record():
    error = VenueRejected(code="minimum", message="below minimum")
    assert error.code == "minimum"
    assert "minimum" in str(error)


def test_capabilities_may_not_claim_leverage_or_a_non_spot_market():
    with pytest.raises(ValidationError):
        VenueCapabilities(
            venue_id="x",
            quote_currencies=["IDR"],
            markets=["SPOT"],
            supports_ws_public=True,
            supports_ws_private=True,
            supports_client_order_id=True,
            self_trade_prevention=True,
            native_stop_orders=False,
            supports_order_modify=False,
            max_leverage=3,  # type: ignore[arg-type]
            regulator="OJK",
        )
    with pytest.raises(ValidationError):
        VenueCapabilities(
            venue_id="x",
            quote_currencies=["IDR"],
            markets=["FUTURES"],  # type: ignore[list-item]
            supports_ws_public=True,
            supports_ws_private=True,
            supports_client_order_id=True,
            self_trade_prevention=True,
            native_stop_orders=False,
            supports_order_modify=False,
            max_leverage=1,
            regulator="OJK",
        )


def test_the_indodax_profile_is_honest_about_its_assumptions():
    profile = INDOdax_PROFILE
    assert isinstance(profile, VenueProfile)
    assert profile.native_stop_orders is False
    assert profile.cfx_fee_pct is None
    assert profile.cfx_fee_pct_assumed > 0
    assert profile.tax_buy_pct_assumed > 0
    assert profile.cost_model_complete() is False
    assert profile.max_leverage == 1
    assert profile.min_notional_idr == Decimal("10000")
    assert profile.tax_applies_to == "SELL_ONLY"


def test_a_limit_order_without_a_price_never_reaches_the_venue():
    with pytest.raises(ValidationError, match="LIMIT order requires limit_price"):
        PlaceOrderRequest(
            pair="btc_idr",
            side="BUY",
            type="LIMIT",
            quantity=Decimal("0.001"),
            client_order_id="atlas-abc",
            signed_order_id="verdict-1",
        )


def test_every_order_carries_its_signed_order_and_client_order_id():
    with pytest.raises(ValidationError):
        PlaceOrderRequest(
            pair="btc_idr",
            side="BUY",
            type="MARKET",
            quantity=Decimal("0.001"),
            client_order_id="",
            signed_order_id="verdict-1",
        )


def test_a_crossed_book_is_refused_before_anyone_prices_a_trade():
    with pytest.raises(ValueError, match="crossed"):
        OrderBook(
            pair="btc_idr",
            bids=[(Decimal("101"), Decimal("1"))],
            asks=[(Decimal("100"), Decimal("1"))],
            venue_ts=1,
        ).validate_book()


def test_a_ticker_cannot_be_built_without_venue_and_ingest_timestamps():
    payload = dict(
        pair="btc_idr",
        bid=Decimal("1"),
        ask=Decimal("2"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
        last=Decimal("1"),
    )
    with pytest.raises(ValidationError):
        Ticker(**payload)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Ticker(**payload, venue_ts=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("method", sorted(PORT_METHODS))
def test_every_port_method_is_async(method):
    bound = getattr(IndodaxPublicVenue, method, None)
    if bound is None:
        pytest.skip(f"{method} not implemented by the public adapter yet")
    assert inspect.iscoroutinefunction(bound) or inspect.isasyncgenfunction(bound)
