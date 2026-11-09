"""Screen untrusted feed text for instructions aimed at the model (FR-010, constitution VI).

The screen is conservative in one direction only: a match excludes the item from every
prompt, and the stored flags say which rule fired. It never rewrites the text — a reader
must be able to see exactly what the feed said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCREEN_VERSION = "1"

# Rule id -> patterns. Ids are stable: they are stored in news_item.injection_flags and
# appear in the operator UI, so renaming one is a schema-visible change.
PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "IGNORE_INSTRUCTIONS": (
        re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions?\b", re.I),
        re.compile(
            r"\bdisregard\s+(your|all|the)\s+(\w+\s+)?(rules?|instructions?|limits?)\b", re.I
        ),
        re.compile(r"\babaikan\s+(semua\s+)?instruksi\b", re.I),
    ),
    "SYSTEM_TAG": (
        re.compile(r"<\s*/?\s*(system|assistant|tool)\s*>", re.I),
        re.compile(r"\[\[\s*(system|instruction)\s*\]\]", re.I),
    ),
    "TRADING_IMPERATIVE": (
        re.compile(r"\b(buy|sell|long|short)\s+(now|immediately|today)\b", re.I),
        re.compile(r"\b(beli|jual)\s+(sekarang|langsung)\b", re.I),
        re.compile(r"\bguaranteed\s+\d+x\b", re.I),
    ),
    "FUND_TRANSFER": (
        re.compile(r"\btransfer\s+(funds?|money|btc|usdt)\b", re.I),
        re.compile(r"\bsend\s+(funds?|money|btc|usdt)\b", re.I),
        re.compile(r"\bkirim\s+(dana|uang)\b", re.I),
    ),
}


@dataclass(frozen=True, slots=True)
class ScreenResult:
    suspected: bool
    flags: tuple[str, ...]


def screen(*parts: str) -> ScreenResult:
    """Match every rule against every part; report rule ids in a stable order."""
    text = "\n".join(parts)
    if not text.strip():
        return ScreenResult(suspected=False, flags=())
    matched = tuple(
        rule_id
        for rule_id, patterns in PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    )
    return ScreenResult(suspected=bool(matched), flags=matched)
