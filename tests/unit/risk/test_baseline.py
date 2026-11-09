"""T060: the day's opening equity is read, never recomputed, and a stale row is refused."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_risk.baseline import JAKARTA, Baseline, StaleBaseline, check, jakarta_date

NOW = datetime(2024, 5, 1, 3, 0, tzinfo=UTC)  # 10:00 in Jakarta


def _baseline(**overrides):
    values = {
        "mode_stage": "PAPER",
        "live_stage": None,
        "day_start_equity_idr": Decimal("1000000"),
        "peak_equity_idr": Decimal("1000000"),
        "stage_started_at": datetime(2024, 5, 1, 0, 30, tzinfo=JAKARTA),
    }
    values.update(overrides)
    return Baseline(**values)


def test_a_baseline_from_today_passes_through() -> None:
    baseline = _baseline()
    assert check(baseline, now=NOW, mode_stage="PAPER") is baseline


def test_a_missing_baseline_is_refused_rather_than_derived() -> None:
    with pytest.raises(StaleBaseline, match="no equity_baseline row"):
        check(None, now=NOW, mode_stage="PAPER")


def test_a_baseline_for_another_stage_is_refused() -> None:
    with pytest.raises(StaleBaseline, match="not CO_PILOT"):
        check(_baseline(), now=NOW, mode_stage="CO_PILOT")


def test_yesterdays_baseline_is_refused_even_when_it_is_recent() -> None:
    yesterday = _baseline(stage_started_at=datetime(2024, 4, 30, 12, 0, tzinfo=UTC))
    with pytest.raises(StaleBaseline, match="cannot price a decision on 2024-05-01"):
        check(yesterday, now=NOW, mode_stage="PAPER")


def test_a_baseline_from_before_today_is_refused_by_the_date_check_first() -> None:
    """The date check subsumes the age window: a row from yesterday is stale however recent."""
    old = _baseline(stage_started_at=NOW - timedelta(hours=27))
    with pytest.raises(StaleBaseline, match="cannot price a decision on 2024-05-01"):
        check(old, now=NOW, mode_stage="PAPER")


def test_jakarta_date_is_utc_plus_seven() -> None:
    assert jakarta_date(datetime(2024, 5, 1, 17, 30, tzinfo=UTC)).isoformat() == "2024-05-02"
