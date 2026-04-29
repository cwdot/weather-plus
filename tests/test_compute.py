"""Pure unit tests for the forecast aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.weather_plus.coordinator import (
    _classify,
    _compute,
    _resolve_anchors,
)

_NOW = datetime(2026, 4, 18, 13, 0, tzinfo=UTC)


def _at(hour: int, day_offset: int = 0) -> datetime:
    return _NOW.replace(hour=hour, minute=0) + timedelta(days=day_offset)


def _fc(when: datetime, temp: float | None) -> dict:
    return {"datetime": when.isoformat(), "temperature": temp}


def _fixed_anchors(now: datetime = _NOW) -> tuple[datetime, datetime, datetime, datetime]:
    return _resolve_anchors(now, None, None, None, 6, 12, 20)


def test_partitions_by_window():
    m, d, n, next_m = _fixed_anchors()
    forecast = [
        _fc(_at(2), 50),  # before today's morningtime → outside cycle
        _fc(_at(8), 60),  # morningtime
        _fc(_at(14), 75),  # daytime
        _fc(_at(20), 65),  # nighttime (start inclusive)
        _fc(_at(23), 55),  # nighttime
    ]
    stats = _compute(forecast, m, d, n, next_m, "°F", _NOW)
    assert stats.todays_high == 75
    assert stats.todays_low == 55
    assert stats.morningtime_low == 60
    assert stats.daytime_high == 75
    assert stats.nighttime_low == 55
    assert stats.temperature_unit == "°F"


def test_includes_post_midnight_in_nighttime_window():
    """Nighttime spans dusk → next morningtime, crossing midnight."""
    m, d, n, next_m = _fixed_anchors()
    forecast = [
        _fc(_at(22), 60),  # tonight nighttime
        _fc(_at(2, day_offset=1), 50),  # post-midnight, still in tonight's nighttime
        _fc(_at(6, day_offset=1), 55),  # tomorrow's morningtime → outside this cycle
    ]
    stats = _compute(forecast, m, d, n, next_m, None, _NOW)
    assert stats.nighttime_low == 50
    assert stats.todays_low == 50


def test_skips_invalid_points():
    m, d, n, next_m = _fixed_anchors()
    forecast = [
        {"datetime": _at(14).isoformat(), "temperature": 70},
        {"datetime": None, "temperature": 80},
        {"datetime": "garbage", "temperature": 90},
        {"datetime": _at(15).isoformat(), "temperature": None},
        {"temperature": 100},
    ]
    stats = _compute(forecast, m, d, n, next_m, None, _NOW)
    assert stats.daytime_high == 70
    assert stats.todays_high == 70


def test_empty_buckets_yield_none():
    m, d, n, next_m = _fixed_anchors()
    forecast = [_fc(_at(14), 75)]
    stats = _compute(forecast, m, d, n, next_m, None, _NOW)
    assert stats.daytime_high == 75
    assert stats.morningtime_low is None
    assert stats.nighttime_low is None


def test_empty_forecast():
    m, d, n, next_m = _fixed_anchors()
    stats = _compute([], m, d, n, next_m, None, _NOW)
    assert stats.todays_high is None
    assert stats.morningtime_low is None
    assert stats.daytime_high is None
    assert stats.nighttime_low is None


def test_window_boundaries_are_half_open():
    m, d, n, next_m = _fixed_anchors()
    forecast = [
        _fc(_at(6), 60),  # morningtime (start inclusive)
        _fc(_at(12), 75),  # daytime (start inclusive)
        _fc(_at(20), 70),  # nighttime (start inclusive)
    ]
    stats = _compute(forecast, m, d, n, next_m, None, _NOW)
    assert stats.morningtime_low == 60
    assert stats.daytime_high == 75
    assert stats.nighttime_low == 70


def test_sun_anchors_override_fixed_hours():
    dawn = _at(7)
    noon = _at(13)
    dusk = _at(19)
    m, d, n, next_m = _resolve_anchors(_NOW, dawn, noon, dusk, 0, 0, 0)
    assert m == dawn
    assert d == noon
    assert n == dusk
    forecast = [
        _fc(_at(6), 50),  # before dawn → outside
        _fc(_at(7), 55),  # dawn → morningtime
        _fc(_at(13), 75),  # noon → daytime
        _fc(_at(19), 65),  # dusk → nighttime
        _fc(_at(22), 58),  # nighttime
    ]
    stats = _compute(forecast, m, d, n, next_m, None, _NOW)
    assert stats.morningtime_low == 55
    assert stats.daytime_high == 75
    assert stats.nighttime_low == 58


def test_current_temperature_passthrough():
    m, d, n, next_m = _fixed_anchors()
    stats = _compute([], m, d, n, next_m, "°F", _NOW, current_temperature=72.5)
    assert stats.current_temperature == 72.5


def test_resolve_anchors_fixed_after_morningtime():
    """At noon, today's anchors are active and night ends tomorrow at morningtime."""
    m, d, n, next_m = _resolve_anchors(_NOW, None, None, None, 6, 12, 20)
    assert m == _at(6)
    assert d == _at(12)
    assert n == _at(20)
    assert next_m == _at(6, day_offset=1)


def test_resolve_anchors_fixed_before_morningtime():
    """At 3am, we're still in yesterday's cycle — anchors slide back a day."""
    pre_dawn = _NOW.replace(hour=3)
    m, d, n, next_m = _resolve_anchors(pre_dawn, None, None, None, 6, 12, 20)
    assert m == pre_dawn.replace(hour=6) - timedelta(days=1)
    assert next_m == pre_dawn.replace(hour=6)
    # the post-midnight 3am sits inside the previous night window
    assert _classify(pre_dawn, m, d, n, next_m) == 2


def test_classify_outside_cycle_returns_none():
    m, d, n, next_m = _fixed_anchors()
    assert _classify(_at(5), m, d, n, next_m) is None  # before morningtime
    assert _classify(_at(6, day_offset=1), m, d, n, next_m) is None  # next morningtime exclusive
