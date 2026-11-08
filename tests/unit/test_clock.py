"""Clock and the 00:00 WIB day boundary (T012, FR-066).

The boundary cases are chosen around 17:00 UTC, which is exactly midnight in Jakarta
(UTC+7, no DST since 1964). A UTC-based day would put 16:59:59 UTC and 17:00:00 UTC on the
same "trading day" — one of them would be a loss counted against the wrong budget.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from atlas_common.clock import (
    FixedClock,
    SystemClock,
    day_start_wib,
    heartbeat_age_seconds,
    next_day_start_wib,
    to_wib,
    trading_day,
)


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_wib_offset_is_seven_hours() -> None:
    assert to_wib(_utc(2026, 11, 8, 10, 0)) == datetime(
        2026, 11, 8, 17, 0, tzinfo=to_wib(_utc(2026, 11, 8)).tzinfo
    )


def test_one_second_before_midnight_is_the_previous_trading_day() -> None:
    instant = _utc(2026, 11, 8, 16, 59, 59)  # 23:59:59 WIB
    assert trading_day(instant).isoformat() == "2026-11-08"


def test_midnight_wib_starts_a_new_trading_day() -> None:
    instant = _utc(2026, 11, 8, 17, 0, 0)  # 00:00:00 WIB on the 9th
    assert trading_day(instant).isoformat() == "2026-11-09"


def test_day_boundary_rolls_over_exactly_at_1700_utc() -> None:
    before = trading_day(_utc(2026, 11, 8, 16, 59, 59))
    after = trading_day(_utc(2026, 11, 8, 17, 0, 0))
    assert after == before + timedelta(days=1)


def test_day_start_is_midnight_wib() -> None:
    start = day_start_wib(_utc(2026, 11, 8, 3, 0))
    assert start.hour == 0
    assert start.minute == 0
    assert start.utcoffset() == timedelta(hours=7)
    assert trading_day(start) == start.date()


def test_next_day_start_is_24_hours_later_for_jakarta() -> None:
    instant = _utc(2026, 11, 8, 3, 0)
    start = day_start_wib(instant)
    following = next_day_start_wib(instant)
    assert following - start == timedelta(days=1)
    assert trading_day(following) == trading_day(instant) + timedelta(days=1)


def test_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        trading_day(datetime(2026, 11, 8, 12, 0))


def test_timezone_aware_input_in_another_zone_is_converted() -> None:
    jakarta = to_wib(_utc(2026, 11, 8, 17, 30))  # 00:30 WIB on the 9th
    assert jakarta.date().isoformat() == "2026-11-09"


def test_system_clock_is_aware_and_monotonic() -> None:
    clock = SystemClock()
    assert clock.now().tzinfo is not None
    first = clock.monotonic()
    second = clock.monotonic()
    assert second >= first


def test_fixed_clock_advances_wall_and_monotonic_together() -> None:
    clock = FixedClock(_utc(2026, 11, 8, 16, 59, 0))
    assert trading_day(clock.now()).isoformat() == "2026-11-08"
    clock.advance(120)
    assert trading_day(clock.now()).isoformat() == "2026-11-09"
    assert clock.monotonic() == 120


def test_fixed_clock_rejects_naive_input() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 11, 8, 12, 0))


def test_heartbeat_age_uses_monotonic() -> None:
    clock = FixedClock(_utc(2026, 11, 8, 0, 0, 0))
    clock.advance(29)
    assert heartbeat_age_seconds(0.0, clock) == pytest.approx(29.0)


def test_heartbeat_from_a_future_monotonic_origin_clamps_to_zero() -> None:
    clock = FixedClock(_utc(2026, 11, 8, 0, 0, 0))
    # A heartbeat written by a process with a different monotonic origin.
    assert heartbeat_age_seconds(1000.0, clock) == 0.0


def test_stop_monitor_threshold_is_reachable_from_the_clock() -> None:
    """30 s stale ⇒ the watchdog must fire (FR-067). The clock must be able to express it."""
    clock = FixedClock(_utc(2026, 11, 8, 0, 0, 0))
    clock.advance(31)
    assert heartbeat_age_seconds(0.0, clock) > 30
