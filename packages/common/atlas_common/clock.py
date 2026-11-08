"""Time for ATLAS: one injectable clock, one day boundary, one monotonic source.

Three facts about time decide whether this system is safe:

* The daily-loss boundary is **00:00 WIB** (``Asia/Jakarta``), not UTC and not the server's
  local time. A UTC boundary would let a 07:00 WIB loss fall into "yesterday".
* A heartbeat is measured on a **monotonic** clock. Wall time can jump (NTP step, VM resume,
  manual change); a stop monitor that trusts wall time can believe a dead process is fresh.
* Tests must be able to move time without sleeping, so every caller receives a clock object
  rather than calling ``datetime.now()`` itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "WIB",
    "Clock",
    "FixedClock",
    "SystemClock",
    "day_start_wib",
    "heartbeat_age_seconds",
    "next_day_start_wib",
    "to_wib",
    "trading_day",
]

WIB: ZoneInfo | timezone
try:  # pragma: no cover - exercised only when the host ships no tzdata
    WIB = ZoneInfo("Asia/Jakarta")
except ZoneInfoNotFoundError:  # pragma: no cover
    # Indonesia has observed no DST since 1964, so a fixed +07:00 is exact for Jakarta.
    # This fallback exists so a container without tzdata still gets the right day, not so
    # that the rest of the codebase may assume naive offsets.
    WIB = timezone(timedelta(hours=7), "WIB")

_MIDNIGHT = dt_time(0, 0)


class Clock(Protocol):
    """The only source of time in application code."""

    def now(self) -> datetime:
        """Timezone-aware wall time in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary, non-decreasing origin. Only differences matter."""
        ...


class SystemClock:
    """Real time. Freshness and heartbeats use ``monotonic``."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass
class FixedClock:
    """Deterministic clock for tests and replay.

    ``monotonic`` follows ``now`` so a test that advances wall time also ages heartbeats,
    without ever sleeping.
    """

    _now: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    _mono: float = 0.0

    def __post_init__(self) -> None:
        if self._now.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock.set needs a timezone-aware datetime")
        self._now = moment

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(
            "naive datetime is ambiguous in a system with a WIB day boundary; "
            "attach a timezone explicitly"
        )
    return moment


def to_wib(moment: datetime) -> datetime:
    """Convert an aware datetime to Jakarta time."""
    return _require_aware(moment).astimezone(WIB)


def trading_day(moment: datetime) -> date:
    """The ATLAS trading day (00:00-23:59 WIB) that ``moment`` belongs to."""
    return to_wib(moment).date()


def day_start_wib(moment: datetime) -> datetime:
    """Start of the WIB day containing ``moment``, as an aware datetime in WIB."""
    return datetime.combine(trading_day(moment), _MIDNIGHT, tzinfo=WIB)


def next_day_start_wib(moment: datetime) -> datetime:
    """Start of the following WIB day — the instant the daily loss budget resets."""
    wib_now = to_wib(moment)
    return datetime.combine(wib_now.date() + timedelta(days=1), _MIDNIGHT, tzinfo=WIB)


def heartbeat_age_seconds(last_seen_monotonic: float, clock: Clock) -> float:
    """Age of a heartbeat in seconds. Negative values clamp to zero.

    Clamping matters: a heartbeat written by a process with a different monotonic origin
    (or before a restart) can look like the future, and "the future" must never be read as
    "very fresh" nor as "very stale" — it is simply zero seconds old.
    """
    age = clock.monotonic() - last_seen_monotonic
    return max(0.0, age)
