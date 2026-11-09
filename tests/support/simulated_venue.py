"""A venue that never leaves the process: the only place M3 may execute.

It is deliberately a full ``VenuePort`` rather than a mock of the methods a test happens to
call, so a test can assert "zero venue requests" and mean it: every request that reached the
venue is in ``orders``, including the ones that ended in a rejection or a timeout.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

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
    VenueRejected,
    VenueSchemaError,
    VenueTrade,
    VenueUnavailable,
)

ZERO = Decimal("0")


@dataclass
class RecordedOrder:
    """What the venue was asked to do, whether or not it agreed."""

    request: PlaceOrderRequest
    venue_order_id: str | None
    status: str


@dataclass
class _OrderState:
    order: VenueOrder
    trades: list[VenueTrade] = field(default_factory=list)


def _default_profile() -> VenueProfile:
    return VenueProfile(
        venue_id="sim",
        version=1,
        fee_maker_pct=Decimal("0.00100"),
        fee_taker_pct=Decimal("0.00300"),
        cfx_fee_pct=Decimal("0.00050"),
        cfx_fee_pct_assumed=Decimal("0.00300"),
        tax_buy_pct_assumed=Decimal("0.00210"),
        tax_pph_pct=Decimal("0.00100"),
        tax_ppn_pct=Decimal("0.11000"),
        tax_applies_to="SELL_ONLY",
        min_notional_idr=Decimal("10000"),
        max_leverage=1,
        native_stop_orders=True,
        cost_model_version="sim-1",
    )


class SimulatedVenue:
    """Deterministic, in-process, single-pair-friendly implementation of ``VenuePort``."""

    def __init__(
        self,
        *,
        venue_id: str = "sim",
        price: Decimal = Decimal("1000"),
        fill_fraction: Decimal = Decimal("1"),
        profile: VenueProfile | None = None,
        pairs: Sequence[str] = ("BTCIDR",),
    ) -> None:
        self.venue_id = venue_id
        self.price = price
        self.fill_fraction = fill_fraction
        self._profile = profile or _default_profile()
        self._pairs = tuple(pairs)
        self.orders: list[RecordedOrder] = []
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self._states: dict[str, _OrderState] = {}
        self._rejects: list[tuple[str, str]] = []
        self._timeouts = 0
        self._ids = itertools.count(1)
        self._clock_ms = 1_700_000_000_000

    # -- programming ---------------------------------------------------------------

    def reject_next(self, code: str, message: str) -> None:
        self._rejects.append((code, message))

    def timeout_next(self) -> None:
        """A request that reached the venue but whose answer never came back."""
        self._timeouts += 1

    def set_price(self, price: Decimal) -> None:
        self.price = price

    def fills(self) -> list[VenueTrade]:
        return [trade for state in self._states.values() for trade in state.trades]

    # -- capabilities --------------------------------------------------------------

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            venue_id=self.venue_id,
            quote_currencies=["IDR"],
            markets=["SPOT"],
            supports_ws_public=True,
            supports_ws_private=False,
            supports_client_order_id=True,
            self_trade_prevention=False,
            native_stop_orders=True,
            supports_order_modify=False,
            max_leverage=1,
            regulator="BAPPEBTI_LEGACY",
        )

    def profile(self) -> VenueProfile:
        return self._profile

    async def pairs(self) -> list[PairInfo]:
        return [
            PairInfo(
                pair=pair,
                base=pair.removesuffix("IDR"),
                quote="IDR",
                tick_size=Decimal("1"),
                step_size=Decimal("0.00000001"),
                min_notional=Decimal("10000"),
                min_qty=Decimal("0.00000001"),
                enabled=True,
            )
            for pair in self._pairs
        ]

    async def balances(self) -> list[Balance]:
        return []

    # -- market data ---------------------------------------------------------------

    def _ticker(self, pair: str, at_ms: int) -> Ticker:
        spread = self.price * Decimal("0.001")
        return Ticker(
            pair=pair,
            bid=self.price - spread,
            ask=self.price + spread,
            last=self.price,
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
            venue_ts=at_ms,
            ingested_at=at_ms,
        )

    async def tickers(self, pairs: Sequence[str] | None = None) -> list[Ticker]:
        wanted = tuple(pairs) if pairs is not None else self._pairs
        unknown = [pair for pair in wanted if pair not in self._pairs]
        if unknown:
            raise VenueSchemaError(f"unknown pair(s): {', '.join(unknown)}")
        return [self._ticker(pair, self._clock_ms) for pair in wanted]

    async def order_book(self, pair: str, depth: int = 20) -> OrderBook:
        ticker = self._ticker(pair, self._clock_ms)
        tick = Decimal("1")
        bids = [(ticker.bid - tick * index, Decimal("1")) for index in range(depth)]
        asks = [(ticker.ask + tick * index, Decimal("1")) for index in range(depth)]
        return OrderBook(pair=pair, bids=bids, asks=asks, venue_ts=self._clock_ms)

    async def recent_trades(self, pair: str, limit: int = 50) -> list[PublicTrade]:
        return [
            PublicTrade(
                pair=pair,
                trade_id=f"sim-pub-{self._clock_ms}",
                price=self.price,
                quantity=Decimal("0.01"),
                side="BUY",
                venue_ts=self._clock_ms,
            )
        ]

    async def bars(self, pair: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
        step = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}.get(timeframe)
        if step is None:
            raise VenueSchemaError(f"unsupported timeframe {timeframe}")
        out: list[Bar] = []
        ts = start_ms - (start_ms % step)
        while ts + step <= end_ms:
            out.append(
                Bar(
                    pair=pair,
                    timeframe=timeframe,
                    ts=ts,
                    open=self.price,
                    high=self.price,
                    low=self.price,
                    close=self.price,
                    volume=Decimal("1"),
                )
            )
            ts += step
        return out

    async def stream_market(self, pairs: Sequence[str]) -> AsyncIterator[MarketEvent]:
        if False:  # pragma: no cover - the type is the point; the sim has no socket
            yield MarketEvent(pair=pairs[0], kind="tick", venue_ts=self._clock_ms)
        await asyncio.sleep(0)

    # -- trading -------------------------------------------------------------------

    async def place_order(self, request: PlaceOrderRequest) -> VenueOrderAck:
        self.place_order_calls += 1
        venue_order_id = f"sim-{next(self._ids)}"
        self.orders.append(
            RecordedOrder(request=request, venue_order_id=venue_order_id, status="SENT")
        )
        if self._timeouts:
            self._timeouts -= 1
            raise VenueUnavailable("simulated timeout: no answer from the venue")
        if self._rejects:
            code, message = self._rejects.pop(0)
            self.orders[-1].status = "REJECTED"
            raise VenueRejected(code, message)

        filled = (request.quantity * self.fill_fraction).quantize(Decimal("0.00000001"))
        if filled <= ZERO:
            raise VenueSchemaError("simulated venue cannot fill zero quantity")
        status: Literal["FILLED", "PARTIALLY_FILLED"] = (
            "FILLED" if filled == request.quantity else "PARTIALLY_FILLED"
        )
        price = request.limit_price or self.price
        trade = VenueTrade(
            pair=request.pair,
            venue_trade_id=f"sim-trade-{len(self.fills()) + 1}",
            order_id=venue_order_id,
            side=request.side,
            quantity=filled,
            price=price,
        )
        order = VenueOrder(
            pair=request.pair,
            venue_order_id=venue_order_id,
            client_order_id=request.client_order_id,
            side=request.side,
            type=request.type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            filled_quantity=filled,
            remaining_quantity=request.quantity - filled,
            status=status,
            avg_fill_price=price,
            venue_ts=self._clock_ms,
        )
        self._states[venue_order_id] = _OrderState(order=order, trades=[trade])
        self.orders[-1].status = status
        return VenueOrderAck(
            pair=request.pair,
            venue_order_id=venue_order_id,
            client_order_id=request.client_order_id,
            status=status,
            venue_ts=self._clock_ms,
        )

    async def cancel_order(self, pair: str, venue_order_id: str) -> VenueOrderAck:
        self.cancel_order_calls += 1
        state = self._states.get(venue_order_id)
        if state is None:
            raise VenueSchemaError(f"unknown order {venue_order_id}")
        self.orders.append(
            RecordedOrder(
                request=PlaceOrderRequest(
                    pair=pair,
                    side=state.order.side,
                    type=state.order.type,
                    quantity=state.order.quantity,
                    limit_price=state.order.limit_price,
                    client_order_id=state.order.client_order_id or "cancel",
                    signed_order_id="cancel",
                ),
                venue_order_id=venue_order_id,
                status="CANCELED",
            )
        )
        state.order = state.order.model_copy(update={"status": "CANCELED"})
        return VenueOrderAck(
            pair=pair,
            venue_order_id=venue_order_id,
            client_order_id=state.order.client_order_id or "cancel",
            status="CANCELED",
            venue_ts=self._clock_ms,
        )

    async def get_order(self, pair: str, venue_order_id: str) -> VenueOrder | None:
        state = self._states.get(venue_order_id)
        return None if state is None else state.order

    async def open_orders(self, pair: str) -> list[VenueOrder]:
        return [
            state.order
            for state in self._states.values()
            if state.order.pair == pair and state.order.status in ("OPEN", "PARTIALLY_FILLED")
        ]

    async def my_trades(self, pair: str, since_ms: int | None = None) -> list[VenueTrade]:
        return [trade for trade in self.fills() if trade.pair == pair]
