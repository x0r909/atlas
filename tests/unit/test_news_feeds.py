"""Free RSS only: the allowlist, the conditional request, and the parse contract.

The allowlist is asserted here so that adding a paid or keyed source is a failing test.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from atlas_news.feeds import (
    FEEDS,
    FeedFetcher,
    FeedParseError,
    MemoryFeedCache,
    NotModified,
    parse_feed,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bitcoin holds above $60k as ETF inflows resume</title>
      <link>https://www.coindesk.com/markets/2024/05/01/btc-60k/?utm_source=rss</link>
      <description>Spot ETF inflows returned after three quiet days.</description>
      <pubDate>Wed, 01 May 2024 09:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Second story</title>
      <link>https://www.coindesk.com/policy/second-story</link>
      <description>Another one.</description>
      <pubDate>Wed, 01 May 2024 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_the_allowlist_is_the_research_list_and_nothing_paid() -> None:
    ids = {feed.source_id for feed in FEEDS}
    assert {
        "coindesk",
        "cointelegraph",
        "decrypt",
        "theblock",
        "bitcoinmagazine",
        "cryptoslate",
        "coinvestasi",
        "ojk",
    } <= ids
    for feed in FEEDS:
        assert feed.url.startswith("https://")
        assert "api_key" not in feed.url and "apikey" not in feed.url and "token=" not in feed.url
        assert 60 <= feed.expected_interval_s <= 120


def test_only_the_indonesian_sources_are_tagged_id() -> None:
    by_id = {feed.source_id: feed for feed in FEEDS}
    assert by_id["coinvestasi"].language == "id"
    assert by_id["ojk"].language == "id"
    assert by_id["coindesk"].language == "en"


def test_parsing_produces_untrusted_items_with_a_published_time() -> None:
    feed = next(feed for feed in FEEDS if feed.source_id == "coindesk")
    items = parse_feed(RSS.encode(), feed)
    assert len(items) == 2
    first = items[0]
    assert first.source_id == "coindesk"
    assert first.title.startswith("Bitcoin holds")
    assert first.url.endswith("?utm_source=rss")
    assert first.published_at is not None
    assert first.published_at.year == 2024
    assert first.language == "en"


def test_a_broken_feed_raises_a_typed_error_instead_of_returning_nothing() -> None:
    feed = FEEDS[0]
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>gateway error</body></html>", feed)


@respx.mock
async def test_the_second_poll_asks_if_nothing_changed() -> None:
    feed = next(feed for feed in FEEDS if feed.source_id == "coindesk")
    route = respx.get(feed.url)
    route.side_effect = [
        httpx.Response(
            200,
            text=RSS,
            headers={"ETag": '"v1"', "Last-Modified": "Wed, 01 May 2024 09:00:00 GMT"},
        ),
        httpx.Response(304),
    ]
    cache = MemoryFeedCache()
    async with httpx.AsyncClient() as client:
        fetcher = FeedFetcher(client, cache)
        first = await fetcher.fetch(feed)
        assert len(first) == 2
        with pytest.raises(NotModified):
            await fetcher.fetch(feed)

    second_request = route.calls[1].request
    assert second_request.headers["If-None-Match"] == '"v1"'
    assert "If-Modified-Since" in second_request.headers


@respx.mock
async def test_an_unexpected_status_is_an_error_not_an_empty_feed() -> None:
    feed = FEEDS[0]
    respx.get(feed.url).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        fetcher = FeedFetcher(client, MemoryFeedCache())
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.fetch(feed)
