"""Venue wire types shared by every adapter and consumer (contracts/venue-adapter.md).

No exchange payload shape appears here: adapters translate into these models, and nothing
outside an adapter knows what Indodax calls a field.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VenueCapabilities(_Model):
    venue_id: str
    quote_currencies: list[str]
    markets: list[Literal["SPOT"]]
    supports_ws_public: bool
    supports_ws_private: bool
    supports_client_order_id: bool
    self_trade_prevention: bool
    native_stop_orders: bool
    supports_order_modify: bool
    max_leverage: Literal[1]
    regulator: Literal["OJK", "BAPPEBTI_LEGACY", "NONE"]

    @field_validator("markets")
    @classmethod
    def _spot_only(cls, value: list[str]) -> list[str]:
        if value != ["SPOT"]:
            raise ValueError("only SPOT markets are supported")
        return value


class PairInfo(_Model):
    pair: str
    base: str
    quote: str
    tick_size: Decimal = Field(gt=0)
    step_size: Decimal = Field(gt=0)
    min_notional: Decimal = Field(gt=0)
    min_qty: Decimal = Field(gt=0)
    enabled: bool


class Ticker(_Model):
    pair: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    bid_size: Decimal = Field(ge=0)
    ask_size: Decimal = Field(ge=0)
    last: Decimal = Field(gt=0)
    venue_ts: int
    ingested_at: int

    @model_validator(mode="after")
    def _not_crossed(self) -> Ticker:
        if self.bid > self.ask:
            raise ValueError("ticker bid above ask")
        return self


class OrderBook(_Model):
    pair: str
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]
    venue_ts: int
    checksum: str | None = None

    @field_validator("bids", "asks")
    @classmethod
    def _non_negative(cls, levels: list[tuple[Decimal, Decimal]]) -> list[tuple[Decimal, Decimal]]:
        for price, qty in levels:
            if price <= 0 or qty < 0:
                raise ValueError("book levels require positive price and non-negative quantity")
        return levels

    def validate_book(self) -> OrderBook:
        if self.bids and self.asks and self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed order book")
        return self


class PublicTrade(_Model):
    pair: str
    trade_id: str
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    side: Literal["BUY", "SELL"]
    venue_ts: int


class Bar(_Model):
    pair: str
    timeframe: str
    ts: int
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    trade_count: int | None = None

    @model_validator(mode="after")
    def _ohlc_coherent(self) -> Bar:
        """A bar that contradicts itself would corrupt every later indicator."""
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is below its own open/close/low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is above its own open/close/high")
        return self


class Balance(_Model):
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class VenueOrder(_Model):
    pair: str
    venue_order_id: str | None = None
    client_order_id: str | None = None
    side: Literal["BUY", "SELL"]
    type: Literal["LIMIT", "MARKET"]
    quantity: Decimal
    limit_price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal | None = None
    status: Literal[
        "PENDING_NEW",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        "UNKNOWN",
    ]
    avg_fill_price: Decimal | None = None
    venue_ts: int
    reject_reason: str | None = None


class VenueOrderAck(_Model):
    pair: str
    venue_order_id: str | None
    client_order_id: str
    status: Literal[
        "PENDING_NEW",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        "UNKNOWN",
    ]
    venue_ts: int
    code: str | None = None
    message: str | None = None


class PlaceOrderRequest(_Model):
    pair: str
    side: Literal["BUY", "SELL"]
    type: Literal["LIMIT", "MARKET"]
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = None
    client_order_id: str = Field(min_length=1)
    # Absent for a protective stop: that order is not the output of a verdict.
    signed_order_id: str | None = None

    @field_validator("limit_price")
    @classmethod
    def _limit_needs_price(cls, value: Decimal | None, info: ValidationInfo) -> Decimal | None:
        order_type = info.data.get("type")
        if order_type == "LIMIT" and value is None:
            raise ValueError("LIMIT order requires limit_price")
        return value

    @model_validator(mode="after")
    def _limit_always_priced(self) -> PlaceOrderRequest:
        # A field validator does not run when the field is absent, so an unpaid LIMIT
        # order would otherwise pass validation and only fail at the venue.
        if self.type == "LIMIT" and self.limit_price is None:
            raise ValueError("LIMIT order requires limit_price")
        return self


class VenueTrade(_Model):
    pair: str
    venue_trade_id: str
    order_id: str | None = None
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    fee_idr: Decimal = Decimal("0")
    tax_idr: Decimal = Decimal("0")
    is_maker: bool | None = None


class VenueProfile(_Model):
    venue_id: str
    version: int
    fee_maker_pct: Decimal
    fee_taker_pct: Decimal
    cfx_fee_pct: Decimal | None = None
    cfx_fee_pct_assumed: Decimal
    tax_buy_pct_assumed: Decimal
    tax_pph_pct: Decimal
    tax_ppn_pct: Decimal
    tax_applies_to: Literal["SELL_ONLY", "BOTH_SIDES"]
    min_notional_idr: Decimal
    max_leverage: Literal[1]
    native_stop_orders: bool
    cost_model_version: str

    def cost_model_complete(self) -> bool:
        return self.cfx_fee_pct is not None


class MarketEvent(_Model):
    """One item from a public market stream."""

    kind: Literal["trade", "ticker", "book", "heartbeat_missed"]
    pair: str
    venue_ts: int
    trade: PublicTrade | None = None
    ticker: Ticker | None = None
    book: OrderBook | None = None


class VenueError(Exception):
    """Base for every mapped adapter failure; no vendor exception escapes an adapter."""


class VenueUnavailable(VenueError):
    pass


class VenueRateLimited(VenueError):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class VenueAuthError(VenueError):
    pass


class VenueRejected(VenueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class VenueNotFound(VenueError):
    pass


class VenueSchemaError(VenueError):
    pass
