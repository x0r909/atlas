"""Dedupe: one syndicated story must become one row (FR-052, R3).

The SimHash threshold is measured, not guessed; see the note on NEAR_DUPLICATE_BITS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from atlas_news.dedupe import (
    DEDUPE_WINDOW_HOURS,
    canonical_url,
    is_near_duplicate,
    simhash64,
)

BASE = datetime(2024, 5, 1, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.coindesk.com/markets/btc-60k/?utm_source=rss&utm_medium=feed",
            "https://coindesk.com/markets/btc-60k",
        ),
        ("HTTPS://CoinDesk.com/Story/Path/", "https://coindesk.com/Story/Path"),
        (
            "https://decrypt.co/news?b=2&a=1&fbclid=xyz",
            "https://decrypt.co/news?a=1&b=2",
        ),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?id=7&utm_campaign=x", "https://example.com/a?id=7"),
    ],
)
def test_canonical_url_strips_what_does_not_change_the_story(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_two_syndicated_titles_hash_close_together() -> None:
    left = simhash64("Bitcoin holds above $60k as ETF inflows resume")
    right = simhash64("Bitcoin holds above $60k as ETF inflows return")
    assert is_near_duplicate(left, right)
    assert not is_near_duplicate(left, simhash64("Ethereum staking yields fall to 3 percent"))


def test_the_window_is_twenty_four_hours() -> None:
    assert DEDUPE_WINDOW_HOURS == 24
    assert timedelta(hours=24) == timedelta(hours=DEDUPE_WINDOW_HOURS)
    assert BASE - timedelta(hours=23) > BASE - timedelta(hours=DEDUPE_WINDOW_HOURS)


def test_empty_title_does_not_match_everything() -> None:
    empty = simhash64("")
    assert empty == 0
    assert not is_near_duplicate(empty, simhash64("anything at all"))
