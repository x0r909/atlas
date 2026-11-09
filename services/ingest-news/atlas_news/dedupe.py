"""Dedupe: the same story from five outlets is one row, keyed by URL and by title.

Two independent keys on purpose (research R3): the canonical URL catches syndication that
keeps the link, the 64-bit SimHash of the title catches rewrites that change it. Both are
cheap and deterministic; embeddings are deliberately *not* used here (they are M6).
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit

DEDUPE_WINDOW_HOURS = 24
# Measured on headline-length text: a syndicated rewrite lands 8-12 bits away, a paraphrase
# 17-24, unrelated stories ~31 (random is 32). 14 sits in the gap. Re-measure before moving it.
NEAR_DUPLICATE_BITS = 14

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "cmpid"})


def canonical_url(raw: str) -> str:
    """Lowercase scheme and host, drop www, tracking params, fragment and a trailing slash."""
    parts = urlsplit(raw.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    kept.sort()
    query = urlencode(kept)

    path = parts.path.rstrip("/") or "/"
    if path == "/":
        path = ""
    canonical = f"{parts.scheme.lower()}://{host}{path}"
    if query:
        canonical = f"{canonical}?{query}"
    return canonical


def _shingles(title: str) -> list[str]:
    """Character trigrams: a one-word rewrite moves a few of them, not half the title.

    Token-level SimHash is unusable on short headlines — swapping one of seven words can
    flip eight bits at 64 bits. Trigram shingles keep syndicated rewrites within a couple
    of bits, which is what the 24 h window needs.
    """
    cleaned = " ".join("".join(char.lower() if char.isalnum() else " " for char in title).split())
    if len(cleaned) < 3:
        return [cleaned] if cleaned else []
    return [cleaned[index : index + 3] for index in range(len(cleaned) - 2)]


def simhash64(text: str) -> int:
    """64-bit SimHash over character trigrams; 0 means "nothing to hash"."""
    tokens = _shingles(text)
    if not tokens:
        return 0

    weights = [0] * 64
    for token in tokens:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if value >> bit & 1 else -1

    result = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            result |= 1 << bit
    return result


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def is_near_duplicate(left: int, right: int) -> bool:
    """Two titles are the same story when at most NEAR_DUPLICATE_BITS bits differ."""
    if left == 0 or right == 0:
        return False
    return hamming(left, right) <= NEAR_DUPLICATE_BITS
