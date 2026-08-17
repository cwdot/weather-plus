"""Pure unit tests for the staged activity 'best time to go outside' computation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.weather_plus.conditions import ForecastPoint
from custom_components.weather_plus.coordinator import (
    ActivitySpec,
    WeatherPlusCoordinator,
    _best_time,
)

# Noon, so today's morning window (6-9) has already passed.
_NOW = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _utc_default_tz():
    """`_best_time` reads the local clock hour; pin local time to UTC for these tests."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(original)


class _StubCoordinator:
    """Exercises _hold_past_pick without standing up Home Assistant."""

    _hold_past_pick = WeatherPlusCoordinator._hold_past_pick

    def __init__(self) -> None:
        self._activity_picks = {}


def _at(hour: int, minute: int = 0, day_offset: int = 0) -> datetime:
    return _NOW.replace(hour=hour, minute=minute) + timedelta(days=day_offset)


def _pt(when: datetime, temp: float | None) -> ForecastPoint:
    return ForecastPoint(when=when, temperature=temp, condition=None)


def _spec(
    start_hour: int,
    end_hour: int,
    *,
    use_temperature: bool = False,
    min_temperature: float = 60,
    max_temperature: float = 75,
    use_elevation: bool = False,
    max_elevation: float = 15,
) -> ActivitySpec:
    return ActivitySpec(
        start_hour=start_hour,
        end_hour=end_hour,
        use_temperature=use_temperature,
        min_temperature=min_temperature,
        max_temperature=max_temperature,
        use_elevation=use_elevation,
        max_elevation=max_elevation,
    )


def _elevation_ramp(start: datetime, degrees_per_hour: float, at_start: float):
    """Linear elevation model: predictable stand-in for astral during unit tests."""

    def fn(when: datetime) -> float:
        return at_start + degrees_per_hour * ((when - start).total_seconds() / 3600)

    return fn


# --- pass 1: time -----------------------------------------------------------


def test_time_pass_bounds_search_to_window():
    points = [
        _pt(_at(12), 72),  # exactly ideal but before window start
        _pt(_at(13), 80),
        _pt(_at(16), 78),
        _pt(_at(18), 72),  # end is exclusive
    ]
    result = _best_time(points, 72, _spec(13, 18), _NOW)
    assert result.best_at is not None
    assert 13 <= dt_util.as_local(result.best_at).hour < 18


def test_time_pass_excludes_past_moments():
    points = [
        _pt(_at(11), 72),  # past, ideal — must be ignored
        _pt(_at(14), 80),
    ]
    result = _best_time(points, 72, _spec(6, 18), _NOW)
    assert result.best_at >= _NOW


def test_time_pass_rolls_to_next_day_when_window_passed():
    """Morning walk window 6-9 is over today, so tomorrow's window is searched."""
    points = [
        _pt(_at(7), 70),  # today's window, but in the past
        _pt(_at(7, day_offset=1), 72),
        _pt(_at(8, day_offset=1), 78),
    ]
    result = _best_time(points, 72, _spec(6, 9), _NOW)
    assert result.best_at == _at(7, day_offset=1)
    assert result.best_temperature == pytest.approx(72)


def test_time_pass_does_not_mix_days():
    """Best moment comes from the earliest qualifying day, even if a later day is closer."""
    points = [
        _pt(_at(13), 78),
        _pt(_at(17), 78),
        _pt(_at(13, day_offset=1), 72),  # exact, but a later day
        _pt(_at(17, day_offset=1), 72),
    ]
    result = _best_time(points, 72, _spec(13, 18), _NOW)
    assert dt_util.as_local(result.best_at).date() == _NOW.date()


def test_time_pass_interpolates_to_sub_hourly_resolution():
    """72 sits midway between the 13:00 and 14:00 points, so 13:30 wins."""
    points = [_pt(_at(13), 70), _pt(_at(14), 74)]
    result = _best_time(points, 72, _spec(13, 18), _NOW)
    assert result.best_at == _at(13, 30)
    assert result.best_temperature == pytest.approx(72)
    assert result.delta_from_ideal == pytest.approx(0)


def test_time_pass_empty_forecast():
    result = _best_time([], 72, _spec(13, 18), _NOW)
    assert result.best_at is None
    assert result.best_temperature is None
    assert result.delta_from_ideal is None


def test_time_pass_no_candidates_yields_empty_result():
    points = [_pt(_at(20), 72), _pt(_at(21), 72)]  # outside the 13-18 window
    result = _best_time(points, 72, _spec(13, 18), _NOW)
    assert result.best_at is None


# --- pass 2: temperature ----------------------------------------------------


def test_temperature_pass_keeps_only_in_range_moments():
    """Ideal 72 is out of range; the closest in-range moment wins instead."""
    points = [_pt(_at(13), 72), _pt(_at(14), 66), _pt(_at(15), 60)]
    spec = _spec(13, 18, use_temperature=True, min_temperature=60, max_temperature=66)
    result = _best_time(points, 72, spec, _NOW)
    assert result.best_temperature <= 66
    assert result.rolled_back == ()


def test_temperature_pass_buffer_rejects_moment_that_heats_up():
    """14:00 hits the ideal exactly, but is 77.5 F twenty minutes later — so 13:40 wins."""
    points = [_pt(_at(13), 70), _pt(_at(14), 75), _pt(_at(15), 90)]
    spec = _spec(13, 18, use_temperature=True, min_temperature=60, max_temperature=76)
    result = _best_time(points, 75, spec, _NOW)
    assert result.best_at == _at(13, 40)
    assert result.best_temperature == pytest.approx(73.333, abs=1e-3)
    assert result.rolled_back == ()


def test_temperature_pass_rolls_back_when_nothing_in_range():
    points = [_pt(_at(13), 95), _pt(_at(14), 96), _pt(_at(15), 97)]
    spec = _spec(13, 18, use_temperature=True, min_temperature=60, max_temperature=75)
    result = _best_time(points, 72, spec, _NOW)
    assert result.rolled_back == ("temperature",)
    assert result.best_temperature == pytest.approx(95)  # closest to ideal despite the range


def test_temperature_pass_skipped_when_disabled():
    points = [_pt(_at(13), 95), _pt(_at(14), 96)]
    result = _best_time(points, 72, _spec(13, 18, use_temperature=False), _NOW)
    assert result.rolled_back == ()
    assert result.best_temperature == pytest.approx(95)


# --- pass 3: elevation ------------------------------------------------------


def test_elevation_pass_rejects_moments_above_the_cap():
    """Sun reaches the 15-degree cap at 14:00, so nothing later than 13:40 survives."""
    points = [_pt(_at(13), 80), _pt(_at(15), 70)]
    elevation = _elevation_ramp(_at(13), degrees_per_hour=10, at_start=5)
    spec = _spec(13, 18, use_elevation=True, max_elevation=15)
    result = _best_time(points, 72, spec, _NOW, elevation)
    assert result.best_at <= _at(13, 40)
    assert result.best_elevation <= 15
    assert result.rolled_back == ()


def test_elevation_pass_buffer_rejects_moment_that_climbs_past_cap():
    """13:50 sits at 13.3 degrees — under the cap — but reaches 16.7 inside the buffer.

    Temperature falls all window, so without the buffer the latest moment would win.
    """
    points = [_pt(_at(13), 80), _pt(_at(15), 70)]
    elevation = _elevation_ramp(_at(13), degrees_per_hour=10, at_start=5)
    spec = _spec(13, 18, use_elevation=True, max_elevation=15)
    result = _best_time(points, 72, spec, _NOW, elevation)
    assert result.best_at == _at(13, 40)
    assert result.best_elevation == pytest.approx(11.667, abs=1e-3)


def test_elevation_pass_rolls_back_to_closest_temperature():
    """Sun is above the cap all window, so elevation is dropped and temperature decides."""
    points = [_pt(_at(13), 90), _pt(_at(14), 70), _pt(_at(15), 85)]
    elevation = _elevation_ramp(_at(13), degrees_per_hour=0, at_start=40)
    spec = _spec(13, 18, use_elevation=True, max_elevation=15)
    result = _best_time(points, 72, spec, _NOW, elevation)
    assert result.rolled_back == ("elevation",)
    assert result.best_at == _at(14, 10)
    assert result.best_temperature == pytest.approx(72.5)


def test_elevation_pass_skipped_without_location():
    points = [_pt(_at(13), 80), _pt(_at(14), 72)]
    spec = _spec(13, 18, use_elevation=True, max_elevation=15)
    result = _best_time(points, 72, spec, _NOW, None)
    assert result.rolled_back == ()
    assert result.best_elevation is None
    assert result.best_at == _at(14)


def test_elevation_pass_skipped_when_disabled():
    points = [_pt(_at(13), 80), _pt(_at(14), 72)]
    elevation = _elevation_ramp(_at(13), degrees_per_hour=0, at_start=40)
    result = _best_time(points, 72, _spec(13, 18, use_elevation=False), _NOW, elevation)
    assert result.rolled_back == ()
    assert result.best_at == _at(14)


# --- passes combined --------------------------------------------------------


def test_morning_walk_picks_optimal_temperature_and_elevation():
    """The stated goal: a 7:30 walk at ~70 F with the sun at ~5 degrees."""
    points = [
        _pt(_at(6, day_offset=1), 64),
        _pt(_at(7, day_offset=1), 68),
        _pt(_at(8, day_offset=1), 72),
        _pt(_at(9, day_offset=1), 76),
    ]
    elevation = _elevation_ramp(_at(7, day_offset=1), degrees_per_hour=10, at_start=5)
    spec = _spec(
        6,
        9,
        use_temperature=True,
        min_temperature=60,
        max_temperature=75,
        use_elevation=True,
        max_elevation=15,
    )
    result = _best_time(points, 70, spec, _NOW, elevation)
    assert result.best_at == _at(7, 30, day_offset=1)
    assert result.best_temperature == pytest.approx(70)
    assert result.best_elevation == pytest.approx(10)
    assert result.rolled_back == ()


def test_evening_walk_uses_the_same_passes():
    """Evening sun descends, so the cap opens up later while temperature falls."""
    points = [
        _pt(_at(17), 88),
        _pt(_at(18), 80),
        _pt(_at(19), 72),
        _pt(_at(20), 66),
    ]
    elevation = _elevation_ramp(_at(17), degrees_per_hour=-10, at_start=35)
    spec = _spec(
        17,
        20,
        use_temperature=True,
        min_temperature=60,
        max_temperature=75,
        use_elevation=True,
        max_elevation=15,
    )
    result = _best_time(points, 70, spec, _NOW, elevation)
    assert result.best_at == _at(19, 20)
    assert result.best_temperature == pytest.approx(70)
    assert result.best_elevation <= 15
    assert result.rolled_back == ()


def test_both_passes_roll_back_independently():
    """Temperature survives, elevation does not; only elevation is reported rolled back."""
    points = [_pt(_at(13), 70), _pt(_at(14), 74)]
    elevation = _elevation_ramp(_at(13), degrees_per_hour=0, at_start=50)
    spec = _spec(
        13,
        18,
        use_temperature=True,
        min_temperature=60,
        max_temperature=75,
        use_elevation=True,
        max_elevation=15,
    )
    result = _best_time(points, 72, spec, _NOW, elevation)
    assert result.rolled_back == ("elevation",)
    assert result.best_at == _at(13, 30)


# --- DST -------------------------------------------------------------------


def test_grid_follows_local_wall_clock_across_dst():
    """A 7am walk stays 7am local when CDT becomes CST, not 6am or 8am.

    2026-11-01 is the US fall-back: 07:00 CDT (UTC-5) on 10-31 and 07:00 CST
    (UTC-6) on 11-01 are 25 hours apart, not 24.
    """
    chicago = ZoneInfo("America/Chicago")
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(chicago)
    try:
        now = datetime(2026, 10, 31, 12, 0, tzinfo=chicago)  # after today's window
        points = [
            ForecastPoint(
                when=datetime(2026, 10, 31, 12, 0, tzinfo=chicago) + timedelta(hours=h),
                temperature=70,
                condition=None,
            )
            for h in range(30)
        ]
        result = _best_time(points, 70, _spec(7, 9), now)
        local = dt_util.as_local(result.best_at)
        assert local.hour == 7
        assert local.date() == date(2026, 11, 1)
        assert local.utcoffset() == timedelta(hours=-6)  # CST, the day after the shift
        # 25 real hours after 07:00 CDT — wall clock held, absolute time did not.
        # Compared in UTC: subtracting two same-tzinfo datetimes is naive arithmetic.
        previous = datetime(2026, 10, 31, 7, 0, tzinfo=chicago)
        assert result.best_at.astimezone(UTC) - previous.astimezone(UTC) == timedelta(hours=25)
    finally:
        dt_util.set_default_time_zone(original)


# --- holding today's pick ---------------------------------------------------


def _morning_points(day: datetime) -> list[ForecastPoint]:
    return [
        _pt(day.replace(hour=h, minute=0), t)
        for h, t in [(5, 58), (6, 62), (7, 68), (8, 74), (9, 80), (10, 86)]
    ]


def test_pick_is_held_once_it_has_passed():
    """Without holding, 07:20 at 70 F degrades to 08:50 at 79 F by mid-morning."""
    coordinator = _StubCoordinator()
    points = _morning_points(_NOW)
    spec = _spec(6, 9, use_temperature=True, min_temperature=50, max_temperature=90)

    picks = []
    for hour, minute in [(5, 30), (6, 0), (7, 0), (8, 0), (8, 45)]:
        now = _NOW.replace(hour=hour, minute=minute)
        fresh = _best_time(points, 70, spec, now)
        picks.append(coordinator._hold_past_pick("activity", fresh, now))

    assert all(p.best_at == _at(7, 20) for p in picks)
    assert all(p.best_temperature == pytest.approx(70) for p in picks)


def test_upcoming_pick_still_tracks_forecast_updates():
    """A pick that has not happened yet must not be frozen — the forecast improves."""
    coordinator = _StubCoordinator()
    spec = _spec(6, 9, use_temperature=True, min_temperature=50, max_temperature=90)
    now = _NOW.replace(hour=6, minute=0)

    first = _best_time(_morning_points(_NOW), 70, spec, now)
    held = coordinator._hold_past_pick("activity", first, now)
    assert held.best_at == _at(7, 20)

    # Revised forecast: the whole morning is cooler, so 70 arrives later.
    revised = [
        _pt(_NOW.replace(hour=h, minute=0), t)
        for h, t in [(5, 52), (6, 56), (7, 62), (8, 68), (9, 74), (10, 80)]
    ]
    second = _best_time(revised, 70, spec, now)
    held = coordinator._hold_past_pick("activity", second, now)
    assert held.best_at == _at(8, 20)


def test_held_pick_is_released_the_next_day():
    """Yesterday's moment must not pin the sensor forever."""
    coordinator = _StubCoordinator()
    spec = _spec(6, 9, use_temperature=True, min_temperature=50, max_temperature=90)

    now = _NOW.replace(hour=8)
    coordinator._hold_past_pick("activity", _best_time(_morning_points(_NOW), 70, spec, now), now)

    tomorrow = _NOW + timedelta(days=1)
    later = tomorrow.replace(hour=5, minute=0)
    fresh = _best_time(_morning_points(tomorrow), 70, spec, later)
    held = coordinator._hold_past_pick("activity", fresh, later)

    assert dt_util.as_local(held.best_at).date() == tomorrow.date()
