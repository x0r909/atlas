"""Ingestion is idempotent or it is wrong (T035, unit half).

The integration test proves the same property against a real hypertable; this one pins the
logic so a regression is caught without Postgres.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest
from atlas_ingest_market.ingest import BAR_STREAM, TICK_STREAM, BarRow, MarketIngestor
from atlas_schemas.events import EventEnvelope
from atlas_schemas.venue import Bar, VenueUnavailable

NOW_MS = 1_718_000_040_000
BAR_MS = 60_000


def bar(pair: str, offset: int, close: str = "1035200000") -> Bar:
    ts = NOW_MS - offset * BAR_MS
    return Bar(
        pair=pair,
        timeframe="1m",
        ts=ts,
        open=Decimal("1034000000"),
        high=Decimal("1036000000"),
        low=Decimal("1033000000"),
        close=Decimal(close),
        volume=Decimal("0.5"),
    )


class FakeBars:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, int], BarRow] = {}
        self.fail = False

    async def upsert_bars(self, rows: Sequence[BarRow]) -> list[BarRow]:
        if self.fail:
            raise RuntimeError("write failed")
        fresh: list[BarRow] = []
        for row in rows:
            key = (row.venue_id, row.pair, row.timeframe, row.ts)
            if key in self.rows:
                continue
            self.rows[key] = row
            fresh.append(row)
        return fresh


class FakeQuotes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Decimal, Decimal, Decimal, int]] = []

    async def upsert_quote(
        self,
        venue_id: str,
        pair: str,
        bid: Decimal,
        ask: Decimal,
        last: Decimal,
        ts: int,
    ) -> None:
        self.calls.append((venue_id, pair, bid, ask, last, ts))


class FakeHealth:
    def __init__(self) -> None:
        self.reports: list[tuple[str, bool, int]] = []

    async def report(
        self, source_id: str, *, ok: bool, expected_interval_s: int, at_ms: int
    ) -> None:
        self.reports.append((source_id, ok, expected_interval_s))


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, EventEnvelope]] = []

    async def publish(self, stream: str, envelope: EventEnvelope) -> str:
        self.published.append((stream, envelope))
        return f"{len(self.published)}-0"


class FakeSource:
    def __init__(self, bars_by_pair: dict[str, list[Bar]], fail: bool = False) -> None:
        self._bars = bars_by_pair
        self._fail = fail

    async def bars(self, pair: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
        if self._fail:
            raise VenueUnavailable("indodax unreachable")
        return [row for row in self._bars.get(pair, []) if start_ms <= row.ts <= end_ms]


def build(
    source: FakeSource, pairs: list[str] | None = None
) -> tuple[MarketIngestor, FakeBars, FakeQuotes, FakeHealth, FakeBus]:
    bars, quotes, health, bus = FakeBars(), FakeQuotes(), FakeHealth(), FakeBus()
    ingestor = MarketIngestor(
        venue_id="indodax",
        source=source,
        bars=bars,
        quotes=quotes,
        health=health,
        bus=bus,
        pairs=pairs or ["btc_idr"],
        now_ms=lambda: NOW_MS,
        stale_after_s=120,
    )
    return ingestor, bars, quotes, health, bus


async def test_a_second_run_writes_and_publishes_nothing_new():
    source = FakeSource({"btc_idr": [bar("btc_idr", 2), bar("btc_idr", 1), bar("btc_idr", 0)]})
    ingestor, bars, _, _, bus = build(source)

    first = await ingestor.run_once()
    published_first = len(bus.published)
    second = await ingestor.run_once()

    assert first > 0
    assert second == 0
    assert len(bus.published) == published_first
    assert len(bars.rows) == first


async def test_the_still_forming_bar_is_never_stored():
    # A bar whose window has not closed yet would ship a partial candle downstream.
    unfinished = Bar(
        pair="btc_idr",
        timeframe="1m",
        ts=NOW_MS - 10_000,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("1.5"),
        volume=Decimal("0.1"),
    )
    ingestor, bars, _, _, bus = build(FakeSource({"btc_idr": [unfinished, bar("btc_idr", 1)]}))
    await ingestor.run_once()
    assert all(row.ts + BAR_MS <= NOW_MS for row in bars.rows.values())
    assert len(bus.published) == 1


async def test_bars_publish_only_after_the_row_exists():
    ingestor, _, _, _, bus = build(FakeSource({"btc_idr": [bar("btc_idr", 1)]}))
    await ingestor.run_once()
    stream, envelope = bus.published[0]
    assert stream == BAR_STREAM
    assert envelope.event_type == "market.bar"
    assert envelope.producer == "ingest-market"
    assert envelope.payload["close"] == "1035200000"
    assert envelope.payload["source"] == "LIVE"


async def test_a_venue_failure_marks_the_source_unhealthy_and_keeps_the_loop_alive():
    ingestor, bars, _, health, bus = build(FakeSource({}, fail=True))
    assert await ingestor.run_once() == 0
    assert bars.rows == {}
    assert bus.published == []
    assert health.reports[0][1] is False
    assert health.reports[0][2] == 60  # expected cadence, not the staleness threshold


async def test_health_is_reported_even_when_nothing_new_arrives():
    ingestor, _, _, health, _ = build(FakeSource({"btc_idr": []}))
    await ingestor.run_once()
    assert health.reports == [("indodax:btc_idr:1m", True, 60)]


async def test_ticks_are_stored_then_published_with_the_quote():
    source = FakeSource({})
    ingestor, _, quotes, _, bus = build(source)
    from atlas_schemas.venue import MarketEvent, Ticker

    ticker = Ticker(
        pair="btc_idr",
        bid=Decimal("1035000000"),
        ask=Decimal("1035500000"),
        bid_size=Decimal("0"),
        ask_size=Decimal("0"),
        last=Decimal("1035200000"),
        venue_ts=NOW_MS,
        ingested_at=NOW_MS,
    )

    async def events():
        yield MarketEvent(kind="ticker", pair="btc_idr", venue_ts=NOW_MS, ticker=ticker)

    published = await ingestor.stream_ticks(events())

    assert published == 1
    assert quotes.calls[0][2:] == (
        Decimal("1035000000"),
        Decimal("1035500000"),
        Decimal("1035200000"),
        NOW_MS,
    )
    stream, envelope = bus.published[0]
    assert stream == TICK_STREAM
    assert envelope.payload["bid"] == "1035000000"


async def test_a_non_tick_event_is_ignored_rather_than_stored():
    ingestor, _, quotes, _, bus = build(FakeSource({}))
    from atlas_schemas.venue import MarketEvent

    async def events():
        yield MarketEvent(kind="heartbeat_missed", pair="btc_idr", venue_ts=NOW_MS)

    assert await ingestor.stream_ticks(events()) == 0
    assert quotes.calls == []
    assert bus.published == []


@pytest.mark.parametrize("pairs", [["btc_idr"], ["btc_idr", "eth_idr"]])
async def test_every_configured_pair_is_ingested(pairs):
    source = {pair: [bar(pair, 1)] for pair in pairs}
    ingestor, bars, _, _, _ = build(FakeSource(source), pairs=pairs)
    await ingestor.run_once()
    assert {row.pair for row in bars.rows.values()} == set(pairs)


async def test_a_write_failure_marks_the_pair_unhealthy_and_is_not_swallowed():
    """A database failure must be visible: the pair cannot keep claiming health."""
    ingestor, bars, _, health, _ = build(FakeSource({"btc_idr": [bar("btc_idr", 1)]}))
    bars.fail = True
    with pytest.raises(RuntimeError, match="write"):
        await ingestor.run_once()
    assert health.reports[0][1] is False
