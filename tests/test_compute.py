"""Pure unit tests for the forecast aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.weather_plus.coordinator import _compute

_NOW = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)


def _at(hour: int, day_offset: int = 0) -> datetime:
    return _NOW.replace(hour=hour, minute=0) + timedelta(days=day_offset)


def _fc(when: datetime, temp: float | None) -> dict:
    return {"datetime": when.isoformat(), "temperature": temp}


def test_partitions_by_window():
    forecast = [
        _fc(_at(2), 50),
        _fc(_at(8), 60),
        _fc(_at(14), 75),
        _fc(_at(20), 65),
        _fc(_at(23), 55),
    ]
    stats = _compute(forecast, 6, 20, "°F", _NOW)
    assert stats.day_high == 75
    assert stats.day_low == 50
    assert stats.daytime_high == 75
    assert stats.daytime_low == 60
    assert stats.night_high == 65
    assert stats.night_low == 50
    assert stats.temperature_unit == "°F"


def test_excludes_other_days():
    forecast = [
        _fc(_at(10), 70),
        _fc(_at(10, day_offset=1), 99),
        _fc(_at(10, day_offset=-1), 1),
    ]
    stats = _compute(forecast, 6, 20, None, _NOW)
    assert stats.day_high == 70
    assert stats.day_low == 70


def test_skips_invalid_points():
    forecast = [
        {"datetime": _at(10).isoformat(), "temperature": 70},
        {"datetime": None, "temperature": 80},
        {"datetime": "garbage", "temperature": 90},
        {"datetime": _at(11).isoformat(), "temperature": None},
        {"temperature": 100},
    ]
    stats = _compute(forecast, 6, 20, None, _NOW)
    assert stats.day_high == 70
    assert stats.day_low == 70


def test_empty_buckets_yield_none():
    forecast = [_fc(_at(2), 50)]
    stats = _compute(forecast, 6, 20, None, _NOW)
    assert stats.day_high == 50
    assert stats.daytime_high is None
    assert stats.daytime_low is None
    assert stats.night_high == 50
    assert stats.night_low == 50


def test_empty_forecast():
    stats = _compute([], 6, 20, None, _NOW)
    assert stats.day_high is None
    assert stats.day_low is None
    assert stats.daytime_high is None
    assert stats.daytime_low is None
    assert stats.night_high is None
    assert stats.night_low is None


def test_window_boundaries_are_half_open():
    forecast = [
        _fc(_at(6), 60),  # daytime (start inclusive)
        _fc(_at(20), 70),  # night (end exclusive)
    ]
    stats = _compute(forecast, 6, 20, None, _NOW)
    assert stats.daytime_high == 60
    assert stats.daytime_low == 60
    assert stats.night_high == 70
    assert stats.night_low == 70
