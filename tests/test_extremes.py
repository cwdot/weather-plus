"""Tests for the carry-forward extremes cache on the coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.weather_plus.coordinator import (
    ForecastStats,
    WeatherPlusCoordinator,
    _DailyExtremes,
)

_NOW = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)


def _make_coordinator() -> WeatherPlusCoordinator:
    """Build a coordinator without invoking DataUpdateCoordinator.__init__."""
    coord = WeatherPlusCoordinator.__new__(WeatherPlusCoordinator)
    coord._extremes = None
    coord.daytime_start = 6
    coord.daytime_end = 20
    return coord


def _stats(**kwargs) -> ForecastStats:
    base = dict(
        day_high=None,
        day_low=None,
        daytime_high=None,
        daytime_low=None,
        night_high=None,
        night_low=None,
        temperature_unit="°F",
    )
    base.update(kwargs)
    return ForecastStats(**base)


def test_initial_merge_seeds_cache():
    coord = _make_coordinator()
    fresh = _stats(
        day_high=80, day_low=60, daytime_high=80, daytime_low=70, night_high=65, night_low=60
    )
    out = coord._merge_extremes(fresh, _NOW, None, None, current=None)
    assert out.day_high == 80
    assert out.day_low == 60
    assert out.daytime_high == 80
    assert out.daytime_low == 70


def test_running_high_does_not_decrease_within_day():
    coord = _make_coordinator()
    coord._merge_extremes(_stats(daytime_high=85, daytime_low=70), _NOW, None, None, current=None)
    # later refresh sees a lower daytime_high (cool morning hours fell out of forecast)
    out = coord._merge_extremes(
        _stats(daytime_high=78, daytime_low=78), _NOW, None, None, current=None
    )
    assert out.daytime_high == 85
    assert out.daytime_low == 70


def test_running_low_does_not_increase_within_day():
    coord = _make_coordinator()
    coord._merge_extremes(_stats(day_low=55, day_high=80), _NOW, None, None, current=None)
    out = coord._merge_extremes(_stats(day_low=70, day_high=82), _NOW, None, None, current=None)
    assert out.day_low == 55
    assert out.day_high == 82


def test_cache_resets_on_new_date():
    coord = _make_coordinator()
    coord._merge_extremes(_stats(day_high=99, day_low=50), _NOW, None, None, current=None)
    next_day = _NOW + timedelta(days=1)
    out = coord._merge_extremes(_stats(day_high=70, day_low=60), next_day, None, None, current=None)
    assert out.day_high == 70
    assert out.day_low == 60


def test_current_temperature_folds_into_day_bucket():
    coord = _make_coordinator()
    out = coord._merge_extremes(_stats(day_high=80, day_low=70), _NOW, None, None, current=82.5)
    assert out.day_high == 82.5
    assert out.day_low == 70


def test_current_temperature_classified_by_sun_window():
    coord = _make_coordinator()
    sunrise = _NOW.replace(hour=7)
    sunset = _NOW.replace(hour=19)
    # _NOW is noon → daytime
    out = coord._merge_extremes(
        _stats(daytime_high=80, daytime_low=72),
        _NOW,
        sunrise,
        sunset,
        current=85,
    )
    assert out.daytime_high == 85
    assert out.night_high is None


def test_current_temperature_classified_by_fixed_hours_when_no_sun():
    coord = _make_coordinator()
    night_time = _NOW.replace(hour=23)
    out = coord._merge_extremes(
        _stats(night_high=70, night_low=60),
        night_time,
        None,
        None,
        current=58,
    )
    assert out.night_low == 58
    assert out.daytime_low is None


def test_none_buckets_in_fresh_do_not_clobber_cache():
    coord = _make_coordinator()
    coord._merge_extremes(_stats(daytime_high=85, daytime_low=72), _NOW, None, None, current=None)
    # next refresh has no daytime points (e.g. all forecast hours fell out)
    out = coord._merge_extremes(
        _stats(daytime_high=None, daytime_low=None), _NOW, None, None, current=None
    )
    assert out.daytime_high == 85
    assert out.daytime_low == 72


def test_extremes_dataclass_initial_state():
    cache = _DailyExtremes(day_date=_NOW.date())
    assert cache.day_high is None
    assert cache.daytime_low is None


def test_reset_extremes_drops_running_values():
    coord = _make_coordinator()
    coord._merge_extremes(_stats(day_high=99, day_low=50), _NOW, None, None, current=None)
    coord.reset_extremes()
    out = coord._merge_extremes(_stats(day_high=70, day_low=60), _NOW, None, None, current=None)
    assert out.day_high == 70
    assert out.day_low == 60


def test_forecast_points_and_unit_pass_through():
    coord = _make_coordinator()
    fresh = _stats(day_high=80, day_low=70, current_temperature=75)
    fresh.forecast_points = []  # explicit
    out = coord._merge_extremes(fresh, _NOW, None, None, current=75)
    # current_temperature flows through unchanged
    assert out.current_temperature == 75
    assert out.temperature_unit == "°F"
