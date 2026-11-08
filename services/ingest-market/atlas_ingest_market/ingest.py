"""Market data ingestion: bars and ticks into Postgres, events onto the bus (T034).

The only hard rule here is that a repeated run changes nothing. Bars are written with
`ON CONFLICT DO NOTHING` on (venue_id, pair, timeframe, ts), and an event is published only
for a row that the insert actually created - so a restart after a crash replays nothing and a
double scheduler tick cannot double-count volume.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from atlas_schemas.events import EventEnvelope
from atlas_schemas.venue import Bar, MarketEvent, VenueError

PRODUCER = "ingest-market"
BAR_STREAM = "atlas:market.bar"
TICK_STREAM = "atlas:market.tick"
DEFAULT_TIMEFRAME = "1m"


@dataclass(frozen=True, slots=True)
class BarRow:
    venue_id: str
    pair: str
    timeframe: str
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str = "LIVE"

    @classmethod
    def from_bar(cls, venue_id: str, bar: Bar, source: str = "LIVE") -> BarRow:
        return cls(
            venue_id=venue_id,
            pair=bar.pair,
            timeframe=bar.timeframe,
            ts=bar.ts,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            source=source,
        )


class EventPublisher(Protocol):
    """The bus surface ingestion needs; the concrete EventBus validates MAXLEN and producer."""

    async def publish(self, stream: str, envelope: EventEnvelope) -> str: ...


class BarWriter(Protocol):
    async def upsert_bars(self, rows: Sequence[BarRow]) -> list[BarRow]:
        """Insert rows, returning only the ones that did not already exist."""
        ...


class QuoteWriter(Protocol):
    async def upsert_quote(
        self, venue_id: str, pair: str, bid: Decimal, ask: Decimal, last: Decimal, ts: int
    ) -> None: ...


class HealthWriter(Protocol):
    async def report(
        self, source_id: str, *, ok: bool, expected_interval_s: int, at_ms: int
    ) -> None: ...


class MarketSource(Protocol):
    async def bars(self, pair: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]: ...


class IngestMetrics(ABC):
    """Counters the service exposes; a silent ingest is indistinguishable from a healthy one."""

    @abstractmethod
    def bars_written(self, count: int) -> None: ...

    @abstractmethod
    def bars_duplicate(self, count: int) -> None: ...

    @abstractmethod
    def source_failure(self, pair: str, exc: Exception) -> None: ...


class NullMetrics(IngestMetrics):
    def bars_written(self, count: int) -> None: ...

    def bars_duplicate(self, count: int) -> None: ...

    def source_failure(self, pair: str, exc: Exception) -> None: ...


def bar_event(venue_id: str, row: BarRow) -> EventEnvelope:
    return EventEnvelope.create(
        "market.bar",
        producer=PRODUCER,
        venue_id=venue_id,
        payload={
            "venue_id": venue_id,
            "pair": row.pair,
            "timeframe": row.timeframe,
            "ts": row.ts,
            "open": str(row.open),
            "high": str(row.high),
            "low": str(row.low),
            "close": str(row.close),
            "volume": str(row.volume),
            "source": row.source,
        },
    )


class MarketIngestor:
    def __init__(
        self,
        *,
        venue_id: str,
        source: MarketSource,
        bars: BarWriter,
        quotes: QuoteWriter,
        health: HealthWriter,
        bus: EventPublisher,
        pairs: Sequence[str],
        timeframe: str = DEFAULT_TIMEFRAME,
        bar_seconds: int = 60,
        stale_after_s: int = 120,
        metrics: IngestMetrics | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.venue_id = venue_id
        self._source = source
        self._bars = bars
        self._quotes = quotes
        self._health = health
        self._bus = bus
        self.pairs = tuple(pairs)
        self.timeframe = timeframe
        self._bar_ms = bar_seconds * 1000
        self._stale_after_s = stale_after_s
        self._metrics = metrics or NullMetrics()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    async def run_once(self, lookback_bars: int = 3) -> int:
        """Fetch the last few closed bars for every pair and publish what is new."""
        end_ms = self._now_ms()
        start_ms = end_ms - lookback_bars * self._bar_ms
        written = 0
        for pair in self.pairs:
            try:
                fetched = await self._source.bars(pair, self.timeframe, start_ms, end_ms)
            except VenueError as exc:
                self._metrics.source_failure(pair, exc)
                await self._health.report(
                    f"{self.venue_id}:{pair}:{self.timeframe}",
                    ok=False,
                    expected_interval_s=self._stale_after_s,
                    at_ms=end_ms,
                )
                continue

            # The newest bar may still be forming; storing it would ship a partial candle.
            closed = [bar for bar in fetched if bar.ts + self._bar_ms <= end_ms]
            rows = [BarRow.from_bar(self.venue_id, bar) for bar in closed]
            if not rows:
                await self._health.report(
                    f"{self.venue_id}:{pair}:{self.timeframe}",
                    ok=True,
                    expected_interval_s=self._stale_after_s,
                    at_ms=end_ms,
                )
                continue

            fresh = await self._bars.upsert_bars(rows)
            self._metrics.bars_written(len(fresh))
            self._metrics.bars_duplicate(len(rows) - len(fresh))
            for row in fresh:
                await self._bus.publish(BAR_STREAM, bar_event(self.venue_id, row))
            written += len(fresh)
            await self._health.report(
                f"{self.venue_id}:{pair}:{self.timeframe}",
                ok=True,
                expected_interval_s=self._stale_after_s,
                at_ms=end_ms,
            )
        return written

    async def stream_ticks(self, events: Any) -> int:
        """Store quotes and publish ticks until the source stream ends."""
        published = 0
        async for event in events:
            if not isinstance(event, MarketEvent) or event.ticker is None:
                continue
            ticker = event.ticker
            await self._quotes.upsert_quote(
                self.venue_id, ticker.pair, ticker.bid, ticker.ask, ticker.last, ticker.venue_ts
            )
            await self._bus.publish(
                TICK_STREAM,
                EventEnvelope.create(
                    "market.tick",
                    producer=PRODUCER,
                    venue_id=self.venue_id,
                    payload={
                        "pair": ticker.pair,
                        "bid": str(ticker.bid),
                        "ask": str(ticker.ask),
                        "last": str(ticker.last),
                        "ts": ticker.venue_ts,
                    },
                ),
            )
            published += 1
        return published


async def serve(
    ingestor: MarketIngestor,
    *,
    interval_s: float = 20.0,
    stop: asyncio.Event | None = None,
    on_error: Callable[[Exception], Awaitable[None]] | None = None,
) -> None:
    """Loop `run_once` forever. A bad cycle is logged, never fatal: data can be refetched."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await ingestor.run_once()
        except Exception as exc:
            if on_error is not None:
                await on_error(exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
