"""Indodax market data over the public REST API (T031).

Only the read-only half of `VenuePort` is implemented here. Order entry needs the private
TAPI endpoints and an API key, which this app is not allowed to create yet; every trading
call therefore raises rather than pretending. Keeping the public adapter separate also means
market data can run with no credentials at all.

Two facts about this venue are guesses and are marked as such in the profile:
`cfx_fee_pct` (Indodax does not publish it) and the buy-side tax rate. `cost_model_complete()`
stays False until a verified sample fill replaces them, and the Risk Engine refuses to size a
trade on an incomplete cost model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from atlas_common.ratelimit import RateLimiter
from atlas_schemas.events import EventEnvelope
from atlas_schemas.venue import (
    Balance,
    Bar,
    MarketEvent,
    OrderBook,
    PairInfo,
    PlaceOrderRequest,
    PublicTrade,
    Ticker,
    VenueAuthError,
    VenueCapabilities,
    VenueNotFound,
    VenueOrder,
    VenueOrderAck,
    VenueProfile,
    VenueRateLimited,
    VenueRejected,
    VenueSchemaError,
    VenueTrade,
    VenueUnavailable,
)

VENUE_ID = "indodax"
REST_BASE = "https://indodax.com"
HISTORY_URL = "https://indodax.com/tradingview/history"

# Chart resolutions are expressed in minutes for intraday frames; Indodax uses "1D" for daily.
RESOLUTIONS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1D"}

INDOdax_PROFILE = VenueProfile(
    venue_id=VENUE_ID,
    version=1,
    fee_maker_pct=Decimal("0.00000"),
    fee_taker_pct=Decimal("0.00300"),
    cfx_fee_pct=None,
    cfx_fee_pct_assumed=Decimal("0.00300"),
    tax_buy_pct_assumed=Decimal("0.00210"),
    tax_pph_pct=Decimal("0.00100"),
    tax_ppn_pct=Decimal("0.11000"),
    tax_applies_to="SELL_ONLY",
    min_notional_idr=Decimal("10000"),
    max_leverage=1,
    native_stop_orders=False,
    cost_model_version="1",
)


def _retry_after_s(raw: str | None) -> float:
    """A hostile or absent Retry-After header must not crash the caller."""
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value > 0 else 1.0


def _dec(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise VenueSchemaError(f"{field}: not a number ({value!r})") from exc


def _ts_ms(value: Any, field: str) -> int:
    try:
        raw = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise VenueSchemaError(f"{field}: not a timestamp ({value!r})") from exc
    # Indodax trades report seconds; the schema everywhere else is milliseconds.
    return raw * 1000 if raw < 10_000_000_000 else raw


class IndodaxPublicVenue:
    venue_id = VENUE_ID
    capabilities = VenueCapabilities(
        venue_id=VENUE_ID,
        quote_currencies=["IDR"],
        markets=["SPOT"],
        supports_ws_public=True,
        supports_ws_private=False,
        supports_client_order_id=False,
        self_trade_prevention=False,
        native_stop_orders=False,
        supports_order_modify=False,
        max_leverage=1,
        regulator="BAPPEBTI_LEGACY",
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
        profile: VenueProfile = INDOdax_PROFILE,
        timeout_s: float = 10.0,
    ) -> None:
        self.profile = profile
        self._limiter = limiter
        self._client = client
        self._owns_client = client is None
        self._timeout_s = timeout_s
        self._pairs_cache: dict[str, PairInfo] | None = None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=REST_BASE, timeout=self._timeout_s, headers={"accept": "application/json"}
            )
            self._owns_client = True
        return self._client

    async def _get_json(self, path: str, *, key: str, params: dict[str, Any] | None = None) -> Any:
        if self._limiter is not None:
            await self._limiter.acquire("public", key=key)
        client = await self._http()
        try:
            response = await client.get(path, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise VenueUnavailable(
                f"{VENUE_ID}: {path} unreachable ({exc.__class__.__name__})"
            ) from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise VenueRateLimited(
                f"{VENUE_ID}: rate limited on {path}",
                retry_after_s=_retry_after_s(retry_after),
            )
        if response.status_code == 404:
            raise VenueNotFound(f"{VENUE_ID}: {path} not found")
        if response.status_code >= 500:
            raise VenueUnavailable(f"{VENUE_ID}: {path} returned {response.status_code}")
        if response.status_code >= 400:
            raise VenueRejected(
                code=str(response.status_code), message=f"{VENUE_ID}: {path} refused the request"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise VenueSchemaError(f"{VENUE_ID}: {path} returned non-JSON body") from exc

    async def pairs(self) -> list[PairInfo]:
        if self._pairs_cache is None:
            raw = await self._get_json("/api/pairs", key="pairs")
            if not isinstance(raw, dict) or not raw:
                raise VenueSchemaError(f"{VENUE_ID}: /api/pairs returned no markets")
            self._pairs_cache = {pair: self._parse_pair(pair, body) for pair, body in raw.items()}
        return list(self._pairs_cache.values())

    async def pair(self, pair: str) -> PairInfo:
        await self.pairs()
        assert self._pairs_cache is not None
        try:
            return self._pairs_cache[pair]
        except KeyError as exc:
            raise VenueNotFound(f"{VENUE_ID}: unknown pair {pair}") from exc

    def _parse_pair(self, pair: str, body: Any) -> PairInfo:
        if not isinstance(body, dict):
            raise VenueSchemaError(f"{VENUE_ID}: pair {pair} is not an object")
        try:
            base = str(body["base_currency"])
            quote = str(body["traded_currency"])
        except KeyError as exc:
            raise VenueSchemaError(f"{VENUE_ID}: pair {pair} missing {exc.args[0]}") from exc
        min_notional = body.get("minimum_total", body.get("minimum_order_size"))
        if min_notional is None:
            raise VenueSchemaError(f"{VENUE_ID}: pair {pair} has no minimum order size")
        return PairInfo(
            pair=pair.lower(),
            base=base.lower(),
            quote=quote.lower(),
            tick_size=_dec(body.get("price_increment", "1"), f"{pair}.price_increment"),
            step_size=_dec(body.get("amount_increment", "0.00000001"), f"{pair}.amount_increment"),
            min_notional=_dec(min_notional, f"{pair}.minimum_total"),
            min_qty=_dec(body.get("minimum_order_size", "0"), f"{pair}.minimum_order_size"),
            enabled=True,
        )

    async def tickers(self, pairs: Sequence[str] | None = None) -> dict[str, Ticker]:
        raw = await self._get_json("/api/ticker_all", key="ticker_all")
        if not isinstance(raw, dict) or not isinstance(raw.get("tickers"), dict):
            raise VenueSchemaError(f"{VENUE_ID}: /api/ticker_all missing 'tickers'")
        wanted = {pair.lower() for pair in pairs} if pairs else None
        out: dict[str, Ticker] = {}
        for pair, body in raw["tickers"].items():
            if wanted is not None and pair.lower() not in wanted:
                continue
            if not isinstance(body, dict):
                raise VenueSchemaError(f"{VENUE_ID}: ticker {pair} is not an object")
            last = _dec(body["last"], f"{pair}.last")
            bid = _dec(body.get("buy", last), f"{pair}.buy")
            ask = _dec(body.get("sell", last), f"{pair}.sell")
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            out[pair.lower()] = Ticker(
                pair=pair.lower(),
                bid=bid,
                ask=ask,
                # The public ticker carries top-of-book prices only; sizes come from the
                # depth call, so a zero here must not be read as an empty side.
                bid_size=Decimal("0"),
                ask_size=Decimal("0"),
                last=last,
                venue_ts=now_ms,
                ingested_at=now_ms,
            )
        return out

    async def order_book(self, pair: str, *, depth: int = 50) -> OrderBook:
        raw = await self._get_json(f"/api/depth/{pair.lower()}", key=f"depth:{pair}")
        if not isinstance(raw, dict):
            raise VenueSchemaError(f"{VENUE_ID}: depth for {pair} is not an object")
        if raw.get("truncated"):
            # A book that was cut off cannot be trusted for sizing: a thin side would
            # look thin instead of incomplete.
            raise VenueSchemaError(f"{VENUE_ID}: depth for {pair} is truncated")
        bids, asks = raw.get("buy"), raw.get("sell")
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise VenueSchemaError(f"{VENUE_ID}: depth for {pair} missing bids or asks")
        book = OrderBook(
            pair=pair.lower(),
            bids=[
                (_dec(level[0], "bid.price"), _dec(level[1], "bid.qty")) for level in bids[:depth]
            ],
            asks=[
                (_dec(level[0], "ask.price"), _dec(level[1], "ask.qty")) for level in asks[:depth]
            ],
            venue_ts=int(datetime.now(UTC).timestamp() * 1000),
        )
        try:
            return book.validate_book()
        except ValueError as exc:
            raise VenueSchemaError(
                f"{VENUE_ID}: depth for {pair} failed validation ({exc})"
            ) from exc

    async def recent_trades(self, pair: str, *, limit: int = 100) -> list[PublicTrade]:
        raw = await self._get_json(f"/api/trades/{pair.lower()}", key=f"trades:{pair}")
        if not isinstance(raw, list):
            raise VenueSchemaError(f"{VENUE_ID}: trades for {pair} is not a list")
        trades = [
            PublicTrade(
                trade_id=str(entry["tid"]),
                pair=pair.lower(),
                price=_dec(entry["price"], "trade.price"),
                quantity=_dec(entry["amount"], "trade.amount"),
                side="BUY" if entry.get("type") == "buy" else "SELL",
                venue_ts=_ts_ms(entry["date"], "trade.date"),
            )
            for entry in raw[:limit]
        ]
        return trades

    async def bars(self, pair: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
        resolution = RESOLUTIONS.get(timeframe)
        if resolution is None:
            raise VenueSchemaError(f"{VENUE_ID}: unsupported timeframe {timeframe}")
        symbol = pair.replace("_", "").upper()
        raw = await self._get_history(symbol, resolution, start_ms, end_ms, pair)
        return self._parse_bars(pair, timeframe, raw)

    async def _get_history(
        self, symbol: str, resolution: str, start_ms: int, end_ms: int, pair: str
    ) -> Any:
        client = await self._http()
        if self._limiter is not None:
            await self._limiter.acquire("public", key=f"history:{pair}")
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": str(start_ms // 1000),
            "to": str(end_ms // 1000),
        }
        try:
            response = await client.get(HISTORY_URL, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise VenueUnavailable(
                f"{VENUE_ID}: history unreachable ({exc.__class__.__name__})"
            ) from exc
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise VenueRateLimited(
                f"{VENUE_ID}: rate limited on history",
                retry_after_s=_retry_after_s(retry_after),
            )
        if response.status_code >= 500:
            raise VenueUnavailable(f"{VENUE_ID}: history returned {response.status_code}")
        if response.status_code >= 400:
            raise VenueRejected(
                code=str(response.status_code), message=f"{VENUE_ID}: history refused"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise VenueSchemaError(f"{VENUE_ID}: history returned non-JSON body") from exc

    def _parse_bars(self, pair: str, timeframe: str, raw: Any) -> list[Bar]:
        if not isinstance(raw, dict):
            raise VenueSchemaError(f"{VENUE_ID}: history body is not an object")
        status = raw.get("s")
        if status == "no_data":
            return []
        if status != "ok":
            raise VenueSchemaError(f"{VENUE_ID}: history status {status!r}")
        series = {key: raw.get(key) for key in ("t", "o", "h", "l", "c", "v")}
        lengths = {len(value) for value in series.values() if isinstance(value, list)}
        if not all(isinstance(value, list) for value in series.values()) or len(lengths) > 1:
            raise VenueSchemaError(f"{VENUE_ID}: history series are ragged or missing")
        count = lengths.pop() if lengths else 0
        bars = []
        for index in range(count):
            try:
                bars.append(
                    Bar(
                        pair=pair.lower(),
                        timeframe=timeframe,
                        ts=int(series["t"][index]) * 1000,  # type: ignore[index]
                        open=_dec(series["o"][index], "bar.open"),  # type: ignore[index]
                        high=_dec(series["h"][index], "bar.high"),  # type: ignore[index]
                        low=_dec(series["l"][index], "bar.low"),  # type: ignore[index]
                        close=_dec(series["c"][index], "bar.close"),  # type: ignore[index]
                        volume=_dec(series["v"][index], "bar.volume"),  # type: ignore[index]
                    )
                )
            except (IndexError, KeyError) as exc:
                raise VenueSchemaError(f"{VENUE_ID}: history row {index} is short") from exc
        return bars

    async def stream_market(
        self, pairs: Sequence[str], poll_s: float = 2.0
    ) -> AsyncIterator[MarketEvent]:
        """Poll tickers with backoff; yields one event per pair per cycle.

        Indodax's public feed is a socket.io v4 channel, which needs a handshake, namespaces
        and its own heartbeats. The consumers of this stream only care that a tick arrives
        with a fresh timestamp, so a poll keeps the same contract without a half-implemented
        protocol; the sequence is monotonic per pair so a gap is visible downstream.
        """
        delay = 1.0
        while True:
            try:
                tickers = await self.tickers(pairs)
            except (VenueUnavailable, VenueRateLimited) as exc:
                wait = (exc.retry_after_s or delay) if isinstance(exc, VenueRateLimited) else delay
                await asyncio.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            delay = 1.0
            for pair in pairs:
                ticker = tickers.get(pair.lower())
                if ticker is None:
                    continue
                yield MarketEvent(
                    kind="ticker", pair=ticker.pair, venue_ts=ticker.venue_ts, ticker=ticker
                )
            await asyncio.sleep(poll_s)

    async def balances(self) -> list[Balance]:
        raise VenueAuthError("public adapter holds no credentials; balances need a private key")

    async def place_order(self, request: PlaceOrderRequest) -> VenueOrderAck:
        raise VenueAuthError(
            "public adapter cannot trade; order entry arrives with the private API"
        )

    async def cancel_order(self, venue_order_id: str, pair: str) -> None:
        raise VenueAuthError("public adapter cannot cancel orders")

    async def get_order(self, venue_order_id: str, pair: str) -> VenueOrder:
        raise VenueAuthError("public adapter cannot read private orders")

    async def open_orders(self, pair: str | None = None) -> list[VenueOrder]:
        raise VenueAuthError("public adapter cannot read private orders")

    async def my_trades(self, pair: str, since_ms: int | None = None) -> list[VenueTrade]:
        raise VenueAuthError("public adapter cannot read private trades")


def ticker_event(venue_id: str, ticker: Ticker) -> EventEnvelope:
    """The bus message for one tick; kept next to the adapter so field names stay in sync."""
    return EventEnvelope.create(
        "market.tick",
        producer="ingest-market",
        venue_id=venue_id,
        payload={
            "pair": ticker.pair,
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "last": str(ticker.last),
            "ts": ticker.venue_ts,
        },
    )
