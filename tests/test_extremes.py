"""Tests for the carry-forward extremes cache on the coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weather_plus.coordinator import (
    ForecastStats,
    WeatherPlusCoordinator,
    _CycleExtremes,
    _resolve_anchors,
)

_NOW = datetime(2026, 4, 23, 13, 0, tzinfo=UTC)


def _make_coordinator() -> WeatherPlusCoordinator:
    """Build a coordinator without invoking DataUpdateCoordinator.__init__."""
    coord = WeatherPlusCoordinator.__new__(WeatherPlusCoordinator)
    coord._extremes = None
    coord._last_success = None
    coord.weather_entity = "weather.test"
    coord.data = None
    coord.morningtime_hour = 6
    coord.daytime_hour = 12
    coord.nighttime_hour = 20
    return coord


def _anchors(now: datetime = _NOW) -> tuple[datetime, datetime, datetime, datetime]:
    return _resolve_anchors(now, None, None, None, 6, 12, 20)


def _stats(**kwargs) -> ForecastStats:
    base = dict(
        todays_high=None,
        todays_low=None,
        morningtime_low=None,
        daytime_high=None,
        nighttime_low=None,
        temperature_unit="°F",
    )
    base.update(kwargs)
    return ForecastStats(**base)


def test_initial_merge_seeds_cache():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    fresh = _stats(
        todays_high=80,
        todays_low=60,
        morningtime_low=60,
        daytime_high=80,
        nighttime_low=62,
    )
    out = coord._merge_extremes(fresh, _NOW, m, d, n, next_m, current=None)
    assert out.todays_high == 80
    assert out.todays_low == 60
    assert out.morningtime_low == 60
    assert out.daytime_high == 80
    assert out.nighttime_low == 62


def test_running_high_does_not_decrease_within_cycle():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(_stats(daytime_high=85), _NOW, m, d, n, next_m, current=None)
    out = coord._merge_extremes(_stats(daytime_high=78), _NOW, m, d, n, next_m, current=None)
    assert out.daytime_high == 85


def test_running_low_does_not_increase_within_cycle():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(
        _stats(todays_low=55, morningtime_low=55), _NOW, m, d, n, next_m, current=None
    )
    out = coord._merge_extremes(
        _stats(todays_low=70, morningtime_low=70), _NOW, m, d, n, next_m, current=None
    )
    assert out.todays_low == 55
    assert out.morningtime_low == 55


def test_cache_resets_on_new_cycle():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(
        _stats(todays_high=99, todays_low=50), _NOW, m, d, n, next_m, current=None
    )
    next_day = _NOW + timedelta(days=1)
    m2, d2, n2, next_m2 = _anchors(next_day)
    out = coord._merge_extremes(
        _stats(todays_high=70, todays_low=60), next_day, m2, d2, n2, next_m2, current=None
    )
    assert out.todays_high == 70
    assert out.todays_low == 60


def test_cache_persists_across_post_midnight_in_same_cycle():
    """3am the next day is still inside the current night window — cache should not reset."""
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(_stats(nighttime_low=55), _NOW, m, d, n, next_m, current=None)
    post_midnight = _NOW.replace(hour=3) + timedelta(days=1)
    m2, d2, n2, next_m2 = _anchors(post_midnight)
    assert m2 == m  # same cycle anchor
    out = coord._merge_extremes(
        _stats(nighttime_low=50),
        post_midnight,
        m2,
        d2,
        n2,
        next_m2,
        current=None,
    )
    assert out.nighttime_low == 50


def test_current_temperature_folds_into_daytime_high_at_noon():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    out = coord._merge_extremes(
        _stats(daytime_high=80),
        _NOW,
        m,
        d,
        n,
        next_m,
        current=85,
    )
    assert out.daytime_high == 85
    assert out.todays_high == 85
    assert out.morningtime_low is None
    assert out.nighttime_low is None


def test_current_temperature_does_not_fold_into_morningtime_high():
    """Morningtime tracks only the low — a warm current temp at dawn must not raise it."""
    coord = _make_coordinator()
    early = _NOW.replace(hour=8)
    m, d, n, next_m = _anchors(early)
    out = coord._merge_extremes(
        _stats(morningtime_low=60),
        early,
        m,
        d,
        n,
        next_m,
        current=70,
    )
    assert out.morningtime_low == 60
    assert out.daytime_high is None  # current temp is in morningtime bucket, not daytime


def test_current_temperature_folds_into_morningtime_low_when_colder():
    coord = _make_coordinator()
    early = _NOW.replace(hour=8)
    m, d, n, next_m = _anchors(early)
    out = coord._merge_extremes(
        _stats(morningtime_low=60),
        early,
        m,
        d,
        n,
        next_m,
        current=55,
    )
    assert out.morningtime_low == 55
    assert out.todays_low == 55


def test_current_temperature_folds_into_nighttime_low_after_dusk():
    coord = _make_coordinator()
    late = _NOW.replace(hour=23)
    m, d, n, next_m = _anchors(late)
    out = coord._merge_extremes(
        _stats(nighttime_low=58),
        late,
        m,
        d,
        n,
        next_m,
        current=55,
    )
    assert out.nighttime_low == 55


def test_none_buckets_in_fresh_do_not_clobber_cache():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(_stats(daytime_high=85), _NOW, m, d, n, next_m, current=None)
    out = coord._merge_extremes(_stats(daytime_high=None), _NOW, m, d, n, next_m, current=None)
    assert out.daytime_high == 85


def test_extremes_dataclass_initial_state():
    cache = _CycleExtremes(cycle_start=_NOW)
    assert cache.todays_high is None
    assert cache.morningtime_low is None
    assert cache.daytime_high is None
    assert cache.nighttime_low is None


def test_reset_extremes_drops_running_values():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    coord._merge_extremes(
        _stats(todays_high=99, todays_low=50), _NOW, m, d, n, next_m, current=None
    )
    coord.reset_extremes()
    out = coord._merge_extremes(
        _stats(todays_high=70, todays_low=60), _NOW, m, d, n, next_m, current=None
    )
    assert out.todays_high == 70
    assert out.todays_low == 60


def test_fallback_raises_when_never_succeeded():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    with pytest.raises(UpdateFailed):
        coord._fallback(RuntimeError("boom"), _NOW, m, d, n, next_m)


def test_fallback_serves_cached_extremes_with_live_current_temp():
    coord = _make_coordinator()
    coord._read_source_state = lambda: ("°F", 72.0)
    m, d, n, next_m = _anchors()
    coord._merge_extremes(
        _stats(todays_high=85, daytime_high=85, todays_low=60, morningtime_low=60),
        _NOW,
        m,
        d,
        n,
        next_m,
        current=None,
    )
    coord._last_success = _NOW

    later = _NOW + timedelta(minutes=15)
    out = coord._fallback(RuntimeError("upstream gone"), later, m, d, n, next_m)

    assert out.daytime_high == 85
    assert out.todays_high == 85
    assert out.morningtime_low == 60
    assert out.current_temperature == 72.0
    assert out.temperature_unit == "°F"
    assert out.morningtime_at == m
    assert out.daytime_at == d
    assert out.nighttime_at == n
    assert out.forecast_points == []


def test_fallback_folds_current_temp_into_extremes():
    """A new high observed during an outage should still update the running max."""
    coord = _make_coordinator()
    coord._read_source_state = lambda: ("°F", 90.0)
    m, d, n, next_m = _anchors()
    coord._merge_extremes(_stats(daytime_high=85), _NOW, m, d, n, next_m, current=None)
    coord._last_success = _NOW

    out = coord._fallback(RuntimeError("flap"), _NOW, m, d, n, next_m)
    assert out.daytime_high == 90.0
    assert out.todays_high == 90.0


def test_fallback_raises_after_stale_window():
    coord = _make_coordinator()
    coord._read_source_state = lambda: ("°F", 70.0)
    coord._last_success = _NOW
    m, d, n, next_m = _anchors()
    with pytest.raises(UpdateFailed):
        coord._fallback(RuntimeError("still gone"), _NOW + timedelta(hours=3), m, d, n, next_m)


def test_fallback_falls_back_to_previous_unit_when_source_missing():
    coord = _make_coordinator()
    coord._read_source_state = lambda: (None, None)
    coord.data = _stats(temperature_unit="°C")
    coord._last_success = _NOW
    m, d, n, next_m = _anchors()
    out = coord._fallback(RuntimeError("ghost"), _NOW, m, d, n, next_m)
    assert out.temperature_unit == "°C"


def test_fallback_resets_extremes_when_cycle_rolls_over():
    """If the parent stays down across the morningtime boundary, old highs shouldn't bleed in."""
    coord = _make_coordinator()
    coord._read_source_state = lambda: ("°F", 50.0)
    m, d, n, next_m = _anchors()
    coord._merge_extremes(_stats(todays_high=99), _NOW, m, d, n, next_m, current=None)
    coord._last_success = _NOW

    next_day = _NOW + timedelta(days=1)
    m2, d2, n2, next_m2 = _anchors(next_day)
    # bump last_success to within the stale window relative to next_day
    coord._last_success = next_day - timedelta(minutes=30)
    out = coord._fallback(RuntimeError("still down"), next_day, m2, d2, n2, next_m2)
    assert out.todays_high == 50.0  # seeded from current temp on the new cycle


def test_forecast_points_and_unit_pass_through():
    coord = _make_coordinator()
    m, d, n, next_m = _anchors()
    fresh = _stats(todays_high=80, todays_low=70, current_temperature=75)
    fresh.forecast_points = []
    out = coord._merge_extremes(fresh, _NOW, m, d, n, next_m, current=75)
    assert out.current_temperature == 75
    assert out.temperature_unit == "°F"
