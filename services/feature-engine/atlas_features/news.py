"""Recency and relevance for a news item, deliberately without embeddings (M6 defers them).

The scores exist so the agent prompt can rank items without the model having to guess which
of forty headlines is recent. Nothing here decides whether an item may enter a prompt: that
is ``excluded_from_prompts`` from the injection screen, and it is copied through untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from atlas_schemas.events import EventEnvelope

PRODUCER = "feature-engine"
ENRICHED_STREAM = "atlas:news.enriched"
ENRICHED_EVENT_TYPE = "news.enriched"

RECENCY_HALF_LIFE_MINUTES = 180.0
MAX_AGE_MINUTES = 24 * 60
KEYWORD_WEIGHT = 0.5
TICKER_WEIGHT = 0.5


@dataclass(frozen=True, slots=True)
class EnrichedNews:
    news_item_id: str
    recency_score: float
    relevance_score: float
    age_minutes: float
    pairs: tuple[str, ...]
    excluded_from_prompts: bool
    injection_flags: tuple[str, ...]


def recency(age_minutes: float) -> float:
    """1.0 for "just published", halving every RECENCY_HALF_LIFE_MINUTES, 0 past MAX_AGE."""
    if age_minutes <= 0:
        return 1.0
    if age_minutes >= MAX_AGE_MINUTES:
        return 0.0
    decayed: float = 0.5 ** (age_minutes / RECENCY_HALF_LIFE_MINUTES)
    return round(decayed, 6)


def relevance(*, tickers: tuple[str, ...], watched: frozenset[str], text: str) -> float:
    """Share of the watched set the item touches, plus a little for market vocabulary."""
    if not watched:
        return 0.0
    hit = sorted(set(tickers) & set(watched))
    ticker_part = TICKER_WEIGHT * (len(hit) / len(watched))
    lowered = text.lower()
    keyword_part = KEYWORD_WEIGHT * min(1.0, sum(word in lowered for word in _KEYWORDS) / 3)
    return round(min(1.0, ticker_part + keyword_part), 6)


_KEYWORDS = ("etf", "regulation", "ban", "hack", "listing", "delisting", "halving", "rate")


def pairs_for(tickers: tuple[str, ...], quote: str = "IDR") -> tuple[str, ...]:
    return tuple(sorted(f"{ticker}{quote}" for ticker in tickers))


def enrich(
    *,
    news_item_id: str,
    title: str,
    summary: str,
    tickers: tuple[str, ...],
    published_at: datetime | None,
    now: datetime,
    watched: frozenset[str],
    excluded_from_prompts: bool,
    injection_flags: tuple[str, ...] = (),
    quote: str = "IDR",
) -> EnrichedNews:
    age_minutes = _age_minutes(published_at, now)
    text = f"{title} {summary}"
    return EnrichedNews(
        news_item_id=news_item_id,
        recency_score=recency(age_minutes),
        relevance_score=relevance(tickers=tickers, watched=watched, text=text),
        age_minutes=round(age_minutes, 3),
        pairs=pairs_for(tickers, quote),
        excluded_from_prompts=excluded_from_prompts,
        injection_flags=injection_flags,
    )


def _age_minutes(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return float(MAX_AGE_MINUTES)
    delta = (now - published_at).total_seconds() / 60.0
    if not math.isfinite(delta):
        return float(MAX_AGE_MINUTES)
    return max(0.0, delta)


class Publisher(Protocol):
    async def publish(self, stream: str, envelope: EventEnvelope) -> Any: ...


def enriched_payload(item: EnrichedNews) -> dict[str, Any]:
    """The wire form. Floats are fine here: these are scores, not money."""
    return {
        "news_item_id": item.news_item_id,
        "recency_score": item.recency_score,
        "relevance_score": item.relevance_score,
        "age_minutes": item.age_minutes,
        "pairs": list(item.pairs),
        "excluded_from_prompts": item.excluded_from_prompts,
        "injection_flags": list(item.injection_flags),
    }


class NewsEnricher:
    """Turns a stored ``news.raw`` payload into ``news.enriched``. No model is involved."""

    def __init__(
        self, publisher: Publisher, *, watched: frozenset[str], quote: str = "IDR"
    ) -> None:
        self._publisher = publisher
        self._watched = watched
        self._quote = quote

    async def handle(self, raw: dict[str, Any], *, now: datetime) -> EnrichedNews:
        published_at = raw.get("published_at")
        item = enrich(
            news_item_id=str(raw["news_item_id"]),
            title=str(raw.get("title", "")),
            summary=str(raw.get("summary", "")),
            tickers=tuple(raw.get("tickers") or ()),
            published_at=datetime.fromisoformat(published_at) if published_at else None,
            now=now,
            watched=self._watched,
            excluded_from_prompts=bool(raw.get("excluded_from_prompts")),
            injection_flags=tuple(raw.get("injection_flags") or ()),
            quote=self._quote,
        )
        payload = enriched_payload(item)
        await self._publisher.publish(
            ENRICHED_STREAM,
            EventEnvelope.create(
                ENRICHED_EVENT_TYPE,
                PRODUCER,
                payload=payload,
                correlation_id=item.news_item_id,
                causation_id=raw.get("event_id"),
            ),
        )
        return item
