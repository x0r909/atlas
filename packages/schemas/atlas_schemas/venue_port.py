"""``VenuePort``: the only way the Execution Service reaches an exchange.

The protocol has no withdraw, transfer, deposit or API-key-minting method, and a structural
test asserts that absence. Adding one is a contract change, not an adapter detail.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlas_schemas.venue import (
    Balance,
    Bar,
    MarketEvent,
    OrderBook,
    PairInfo,
    PlaceOrderRequest,
    PublicTrade,
    Ticker,
    VenueCapabilities,
    VenueOrder,
    VenueOrderAck,
    VenueProfile,
    VenueTrade,
)

__all__ = ["FORBIDDEN_METHODS", "VenuePort", "assert_port_surface"]

FORBIDDEN_METHODS = frozenset(
    {
        "withdraw",
        "withdrawal",
        "withdraw_fiat",
        "transfer",
        "transfer_internal",
        "deposit",
        "deposit_address",
        "create_api_key",
        "make_api_key",
        "enable_withdrawal",
    }
)


@runtime_checkable
class VenuePort(Protocol):
    """Cryptocurrency spot only. Every money-moving call is authenticated and idempotent."""

    @property
    def venue_id(self) -> str: ...

    async def capabilities(self) -> VenueCapabilities: ...

    def profile(self) -> VenueProfile: ...

    async def pairs(self) -> list[PairInfo]: ...

    async def balances(self) -> list[Balance]: ...

    async def tickers(self, pairs: list[str] | None = None) -> list[Ticker]: ...

    async def order_book(self, pair: str, depth: int = 20) -> OrderBook: ...

    async def recent_trades(self, pair: str, limit: int = 100) -> list[PublicTrade]: ...

    async def bars(self, pair: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]: ...

    async def place_order(self, request: PlaceOrderRequest) -> VenueOrderAck: ...

    async def cancel_order(self, pair: str, venue_order_id: str) -> VenueOrderAck: ...

    async def get_order(self, pair: str, venue_order_id: str) -> VenueOrder: ...

    async def open_orders(self, pair: str | None = None) -> list[VenueOrder]: ...

    async def my_trades(self, pair: str, since_ms: int | None = None) -> list[VenueTrade]: ...

    def stream_market(self, pairs: list[str]) -> MarketStream: ...


class MarketStream(Protocol):
    """An async iterator over public market events that reconnects on its own."""

    def __aiter__(self) -> MarketStream: ...

    async def __anext__(self) -> MarketEvent: ...

    async def aclose(self) -> None: ...


def assert_port_surface(port_cls: type) -> None:
    """Fail if a port implementation grew a money-egress method."""
    present = {name for name in dir(port_cls) if not name.startswith("_")}
    forbidden = present & FORBIDDEN_METHODS
    if forbidden:
        raise AssertionError(f"{port_cls.__name__} exposes forbidden methods: {sorted(forbidden)}")
