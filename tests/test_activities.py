"""Pure unit tests for the activity 'best time to go outside' computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.weather_plus.conditions import ForecastPoint
from custom_components.weather_plus.coordinator import _best_time

# Noon, so today's morning window (6-9) has already passed.
_NOW = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _utc_default_tz():
    """`_best_time` reads the local clock hour; pin local time to UTC for these tests."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(original)


def _at(hour: int, day_offset: int = 0) -> datetime:
    return _NOW.replace(hour=hour, minute=0) + timedelta(days=day_offset)


def _pt(when: datetime, temp: float | None) -> ForecastPoint:
    return ForecastPoint(when=when, temperature=temp, condition=None)


def test_picks_hour_closest_to_ideal_within_window():
    points = [
        _pt(_at(13), 80),  # daytime window 13-18
        _pt(_at(14), 74),  # closest to ideal 72
        _pt(_at(15), 68),
    ]
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at == _at(14)
    assert result.best_temperature == 74
    assert result.delta_from_ideal == 2


def test_excludes_hours_outside_window():
    points = [
        _pt(_at(12), 72),  # exactly ideal but before window start
        _pt(_at(18), 72),  # end is exclusive
        _pt(_at(16), 78),  # only one in [13, 18)
    ]
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at == _at(16)
    assert result.best_temperature == 78


def test_excludes_past_hours():
    """An hour earlier than now is not a candidate even if in the clock window."""
    points = [
        _pt(_at(11), 72),  # past, ideal — must be ignored
        _pt(_at(14), 80),
    ]
    result = _best_time(points, 72, 6, 18, _NOW)
    assert result.best_at == _at(14)


def test_rolls_to_next_day_when_window_already_passed():
    """Morning walk window 6-9 is over today, so tomorrow's window is searched."""
    points = [
        _pt(_at(7), 70),  # today's window, but in the past
        _pt(_at(7, day_offset=1), 71),  # tomorrow's window
        _pt(_at(8, day_offset=1), 75),
    ]
    result = _best_time(points, 72, 6, 9, _NOW)
    assert result.best_at == _at(7, day_offset=1)
    assert result.best_temperature == 71


def test_does_not_mix_days():
    """Best hour comes from the earliest qualifying day, even if a later day is closer."""
    points = [
        _pt(_at(14), 78),  # today, delta 6
        _pt(_at(14, day_offset=1), 72),  # tomorrow, exact — but a later day
    ]
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at == _at(14)


def test_ties_resolve_to_earliest_hour():
    points = [
        _pt(_at(15), 74),  # delta 2
        _pt(_at(16), 70),  # delta 2, same day, later hour
    ]
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at == _at(15)


def test_skips_points_without_temperature():
    points = [
        _pt(_at(14), None),
        _pt(_at(15), 73),
    ]
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at == _at(15)


def test_no_candidates_yields_empty_result():
    points = [_pt(_at(20), 72)]  # outside the 13-18 window
    result = _best_time(points, 72, 13, 18, _NOW)
    assert result.best_at is None
    assert result.best_temperature is None
    assert result.delta_from_ideal is None


def test_empty_forecast():
    result = _best_time([], 72, 13, 18, _NOW)
    assert result.best_at is None
