"""Poll every allowlisted feed, screen it, store it, publish it (FR-011, FR-052).

One dead feed must never stop the loop: each feed is isolated, its failure is reported to
``data_health``, and the cycle continues. That is the whole reliability argument of this file.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from atlas_schemas.events import EventEnvelope

from atlas_news.dedupe import DEDUPE_WINDOW_HOURS, canonical_url, simhash64
from atlas_news.feeds import FeedFetcher, FeedSpec, NotModified, RawItem
from atlas_news.injection import SCREEN_VERSION, screen
from atlas_news.store import NewsRow

PRODUCER = "ingest-news"
RAW_STREAM = "atlas:news.raw"
NEWS_EVENT_TYPE = "news.raw"


class NewsStore(Protocol):
    async def find_near_duplicate(self, simhash: int, window_start: datetime) -> Any: ...
    async def insert(self, row: NewsRow) -> Any: ...


class HealthReporter(Protocol):
    async def report(
        self, source_id: str, ok: bool, expected_interval_s: int, at_ms: int
    ) -> None: ...


class Publisher(Protocol):
    async def publish(self, stream: str, envelope: EventEnvelope) -> Any: ...


@dataclass(slots=True)
class CycleStats:
    fetched: int = 0
    not_modified: int = 0
    stored: int = 0
    duplicates: int = 0
    flagged: int = 0
    failed: int = 0
    failures: dict[str, str] = field(default_factory=dict)


class NewsWorker:
    def __init__(
        self,
        *,
        store: NewsStore,
        health: HealthReporter,
        publisher: Publisher,
        fetcher: FeedFetcher,
        feeds: Sequence[FeedSpec],
        window_hours: int = DEDUPE_WINDOW_HOURS,
    ) -> None:
        self._store = store
        self._health = health
        self._publisher = publisher
        self._fetcher = fetcher
        self._feeds = feeds
        self._window = timedelta(hours=window_hours)

    async def run_once(self, *, now: datetime | None = None) -> CycleStats:
        started = now or datetime.now(UTC)
        stats = CycleStats()
        for feed in self._feeds:
            await self._poll(feed, started, stats)
        return stats

    async def _poll(self, feed: FeedSpec, started: datetime, stats: CycleStats) -> None:
        at_ms = int(started.timestamp() * 1000)
        try:
            items = await self._fetcher.fetch(feed)
        except NotModified:
            stats.not_modified += 1
            await self._health.report(feed.source_id, True, feed.expected_interval_s, at_ms)
            return
        except (httpx.HTTPError, ValueError) as exc:
            # One feed's failure is data, not a crash: the remaining feeds still get a turn.
            stats.failed += 1
            stats.failures[feed.source_id] = f"{type(exc).__name__}: {exc}"
            await self._health.report(feed.source_id, False, feed.expected_interval_s, at_ms)
            return

        stats.fetched += len(items)
        await self._health.report(feed.source_id, True, feed.expected_interval_s, at_ms)
        for item in items:
            await self._handle(item, started, stats)

    async def _handle(self, item: RawItem, started: datetime, stats: CycleStats) -> None:
        url_canonical = canonical_url(item.url)
        screened = screen(item.title, item.summary, item.body_text)
        hashed = simhash64(item.title)
        duplicate_of = await self._store.find_near_duplicate(hashed, started - self._window)

        row = NewsRow(
            source_id=item.source_id,
            source_name=item.source_name,
            url=item.url,
            url_canonical=url_canonical,
            title=item.title,
            summary=item.summary,
            body_text=item.body_text,
            language=item.language,
            published_at=item.published_at,
            tickers=_tickers(item),
            injection_flags=screened.flags,
            screen_version=SCREEN_VERSION,
            simhash=hashed,
            duplicate_of=duplicate_of,
        )
        stored_id = await self._store.insert(row)
        if stored_id is None:
            stats.duplicates += 1
            return

        stats.stored += 1
        if screened.suspected:
            stats.flagged += 1
        await self._publisher.publish(
            RAW_STREAM,
            EventEnvelope.create(
                NEWS_EVENT_TYPE,
                PRODUCER,
                payload={
                    "news_item_id": str(stored_id),
                    "source_id": item.source_id,
                    "url": item.url,
                    "title": item.title,
                    "language": item.language,
                    "tickers": list(row.tickers),
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "excluded_from_prompts": screened.suspected,
                    "injection_flags": list(screened.flags),
                    "duplicate_of": str(duplicate_of) if duplicate_of else None,
                },
                correlation_id=str(stored_id),
            ),
        )


def _tickers(item: RawItem) -> tuple[str, ...]:
    """Cheap symbol tagging from the text; the entity model is M6, this is the honest v1."""
    known = ("BTC", "ETH", "SOL", "XRP", "USDT", "BNB", "DOGE", "ADA", "AVAX", "TON")
    haystack = f"{item.title} {item.summary}".upper()
    found = {symbol for symbol in known if _mentions(haystack, symbol)}
    return tuple(sorted(found))


def _mentions(haystack: str, symbol: str) -> bool:
    index = haystack.find(symbol)
    while index != -1:
        after = haystack[index + len(symbol) : index + len(symbol) + 1]
        if not after or not after.isalnum():
            return True
        index = haystack.find(symbol, index + 1)
    return False


async def serve(
    worker: NewsWorker,
    *,
    interval_s: float = 90.0,
    should_stop: Any = None,
    on_cycle: Any = None,
    sleep: Any = None,
) -> None:
    """Poll forever; a failed cycle is reported and the next one still runs."""
    import asyncio

    sleeper = sleep or asyncio.sleep
    while True:
        if should_stop is not None and should_stop():
            return
        started = time.monotonic()
        try:
            stats = await worker.run_once()
        except Exception as exc:
            if on_cycle is not None:
                on_cycle(None, exc)
        else:
            if on_cycle is not None:
                on_cycle(stats, None)
        elapsed = time.monotonic() - started
        await sleeper(max(0.0, interval_s - elapsed))


__all__ = ["NEWS_EVENT_TYPE", "PRODUCER", "RAW_STREAM", "CycleStats", "NewsWorker", "serve"]
