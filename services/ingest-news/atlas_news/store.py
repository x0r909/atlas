"""Storage for news items: URL uniqueness in the database, similarity in Python.

The unique index on ``url_canonical`` is the guarantee; the SimHash scan is a heuristic and
runs over a bounded recent slice so a busy feed list cannot turn it into a full-table walk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from atlas_news.dedupe import is_near_duplicate

RECENT_SCAN_LIMIT = 500
_UINT64 = 1 << 64
_INT64 = 1 << 63


def to_signed(simhash: int) -> int:
    """bigint is signed and a 64-bit SimHash is not; store the same bits, reinterpreted."""
    return simhash - _UINT64 if simhash >= _INT64 else simhash


def to_unsigned(stored: int) -> int:
    return stored + _UINT64 if stored < 0 else stored


@dataclass(frozen=True, slots=True)
class NewsRow:
    source_id: str
    source_name: str
    url: str
    url_canonical: str
    title: str
    summary: str
    body_text: str
    language: str
    published_at: datetime | None
    tickers: tuple[str, ...]
    injection_flags: tuple[str, ...]
    screen_version: str
    simhash: int
    duplicate_of: UUID | None


_INSERT = """
INSERT INTO news_item (
    source_id, source_name, url, url_canonical, title, summary, body_text, language,
    published_at, tickers, injection_flags, screen_version, excluded_from_prompts, simhash,
    duplicate_of
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8,
    $9, $10, CAST($11 AS jsonb), $12, $13, $14,
    $15
)
ON CONFLICT (url_canonical) DO NOTHING
RETURNING id
"""


class AsyncpgNewsStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_near_duplicate(self, simhash: int, window_start: datetime) -> UUID | None:
        if simhash == 0:
            return None
        rows = await self._pool.fetch(
            """
            SELECT id, simhash FROM news_item
            WHERE fetched_at >= $1 AND simhash <> 0
            ORDER BY fetched_at DESC
            LIMIT $2
            """,
            window_start,
            RECENT_SCAN_LIMIT,
        )
        for row in rows:
            if is_near_duplicate(simhash, to_unsigned(int(row["simhash"]))):
                return UUID(str(row["id"]))
        return None

    async def insert(self, row: NewsRow) -> UUID | None:
        """Return the new id, or None when the canonical URL was already stored."""
        flags: list[Any] = list(row.injection_flags)
        inserted: Any = await self._pool.fetchval(
            _INSERT,
            row.source_id,
            row.source_name,
            row.url,
            row.url_canonical,
            row.title,
            row.summary,
            row.body_text,
            row.language,
            row.published_at,
            list(row.tickers),
            json.dumps(flags),
            row.screen_version,
            bool(flags),
            to_signed(row.simhash),
            row.duplicate_of,
        )
        return UUID(str(inserted)) if inserted is not None else None
