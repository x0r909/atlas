"""The day's opening equity, read once and refused when it is stale.

Recomputing a baseline from the current book would let a bad day silently become the new
starting point, which is exactly what the daily-loss breaker exists to prevent (FR-065).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

JAKARTA = timezone(timedelta(hours=7))
MAX_BASELINE_AGE = timedelta(hours=26)


class StaleBaseline(RuntimeError):
    """The stored baseline does not belong to today in Jakarta: refuse, never recompute."""


@dataclass(frozen=True, slots=True)
class Baseline:
    mode_stage: str
    live_stage: str | None
    day_start_equity_idr: Decimal
    peak_equity_idr: Decimal
    stage_started_at: datetime


class BaselineSource(Protocol):
    async def load(self, mode_stage: str) -> Baseline | None: ...


def jakarta_date(now: datetime) -> date:
    return now.astimezone(JAKARTA).date()


def check(baseline: Baseline | None, *, now: datetime, mode_stage: str) -> Baseline:
    if baseline is None:
        raise StaleBaseline(f"no equity_baseline row for {mode_stage}")
    if baseline.mode_stage != mode_stage:
        raise StaleBaseline(f"baseline is for {baseline.mode_stage}, not {mode_stage}")
    started_on = jakarta_date(baseline.stage_started_at)
    if started_on != jakarta_date(now):
        raise StaleBaseline(
            f"baseline dated {started_on.isoformat()} cannot price a decision on "
            f"{jakarta_date(now).isoformat()}"
        )
    if now - baseline.stage_started_at > MAX_BASELINE_AGE:
        raise StaleBaseline(
            f"baseline is {now - baseline.stage_started_at} old, older than {MAX_BASELINE_AGE}"
        )
    return baseline
