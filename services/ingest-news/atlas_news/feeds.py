"""Free RSS feeds only, and only the ones research R3 chose (FR-052).

There is deliberately no paid vendor path and no API key anywhere in this module: every field
of ``FeedSpec`` is public, and ``FEEDS`` is a constant a reviewer can read in one screen.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import feedparser
import httpx

USER_AGENT = "ATLAS/0.1 (+https://github.com/atlas-trader; operator monitoring)"


class FeedParseError(ValueError):
    """The bytes were not a feed with entries. Distinct from "the feed was empty"."""


class NotModified(Exception):
    """The conditional request was answered with 304: nothing to do this cycle."""


@dataclass(frozen=True, slots=True)
class FeedSpec:
    source_id: str
    source_name: str
    url: str
    expected_interval_s: int
    language: str


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 90, "en"),
    FeedSpec("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss", 90, "en"),
    FeedSpec("decrypt", "Decrypt", "https://decrypt.co/feed", 120, "en"),
    FeedSpec("theblock", "The Block", "https://www.theblock.co/rss.xml", 120, "en"),
    FeedSpec(
        "bitcoinmagazine",
        "Bitcoin Magazine",
        "https://bitcoinmagazine.com/feed",
        120,
        "en",
    ),
    FeedSpec("cryptoslate", "CryptoSlate", "https://cryptoslate.com/feed/", 120, "en"),
    FeedSpec("coinvestasi", "Coinvestasi", "https://coinvestasi.com/feed/", 120, "id"),
    FeedSpec(
        "ojk",
        "OJK press releases",
        "https://www.ojk.go.id/id/berita-dan-kegiatan/publikasi/rss",
        120,
        "id",
    ),
)


@dataclass(frozen=True, slots=True)
class RawItem:
    """What the feed said, before anything has been decided about it."""

    source_id: str
    source_name: str
    url: str
    title: str
    summary: str
    body_text: str
    published_at: datetime | None
    language: str


class FeedCache(Protocol):
    async def get(self, source_id: str) -> tuple[str | None, str | None] | None: ...
    async def set(self, source_id: str, etag: str | None, last_modified: str | None) -> None: ...


class MemoryFeedCache:
    """The validator store, small enough that a test can hold it."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[str | None, str | None]] = {}

    async def get(self, source_id: str) -> tuple[str | None, str | None] | None:
        return self._rows.get(source_id)

    async def set(self, source_id: str, etag: str | None, last_modified: str | None) -> None:
        self._rows[source_id] = (etag, last_modified)


class FeedFetcher:
    """One conditional GET per feed per cycle; a 304 is the normal happy path."""

    def __init__(self, client: httpx.AsyncClient, cache: FeedCache) -> None:
        self._client = client
        self._cache = cache

    async def fetch(self, feed: FeedSpec) -> list[RawItem]:
        headers = {"User-Agent": USER_AGENT}
        cached = await self._cache.get(feed.source_id)
        if cached is not None:
            etag, last_modified = cached
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified

        response = await self._client.get(feed.url, headers=headers)
        if response.status_code == httpx.codes.NOT_MODIFIED:
            raise NotModified(feed.source_id)
        response.raise_for_status()

        await self._cache.set(
            feed.source_id, response.headers.get("ETag"), response.headers.get("Last-Modified")
        )
        return parse_feed(response.content, feed)


def parse_feed(payload: bytes, feed: FeedSpec) -> list[RawItem]:
    parsed = feedparser.parse(payload)
    if not parsed.entries:
        reason = getattr(parsed, "bozo_exception", "no entries")
        raise FeedParseError(f"{feed.source_id}: not a feed with entries ({reason})")

    items: list[RawItem] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = (entry.get("summary") or "").strip()
        items.append(
            RawItem(
                source_id=feed.source_id,
                source_name=feed.source_name,
                url=link,
                title=title,
                summary=summary,
                body_text=summary,
                published_at=_published_at(entry),
                language=feed.language,
            )
        )
    return items


def _published_at(entry: dict[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            return datetime.fromtimestamp(calendar.timegm(struct), UTC)
    return None


def feed_by_id(source_id: str) -> FeedSpec:
    for feed in FEEDS:
        if feed.source_id == source_id:
            return feed
    raise KeyError(source_id)


def feeds() -> Sequence[FeedSpec]:
    return FEEDS
