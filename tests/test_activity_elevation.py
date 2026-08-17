"""Elevation pass against the real astral/Home Assistant location path.

The pure tests in test_activities.py inject a linear elevation model. These
exercise the actual `get_astral_location` -> `astral.sun.elevation` wiring the
coordinator uses, so a broken import or observer never passes silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weather_plus.conditions import ForecastPoint
from custom_components.weather_plus.const import (
    CONF_WEATHER_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_ACTIVITY,
)
from custom_components.weather_plus.coordinator import (
    ActivitySpec,
    WeatherPlusCoordinator,
    _best_time,
)

# Denver, mid-June: sunrise is around 05:32 local, so a 6-9 window spans low sun.
_LATITUDE = 39.7392
_LONGITUDE = -104.9903
_TIMEZONE = "America/Denver"


@pytest.fixture
async def denver(hass: HomeAssistant) -> HomeAssistant:
    await hass.config.async_update(
        latitude=_LATITUDE,
        longitude=_LONGITUDE,
        elevation=1609,
        time_zone=_TIMEZONE,
    )
    return hass


def _coordinator(hass: HomeAssistant) -> WeatherPlusCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="weather.home",
        data={CONF_WEATHER_ENTITY: "weather.home"},
    )
    entry.add_to_hass(hass)
    return WeatherPlusCoordinator(hass, entry)


async def test_real_elevation_fn_tracks_the_sun(denver: HomeAssistant) -> None:
    """Solar elevation is negative before sunrise, low at 7am, and high at solar noon."""
    fn = _coordinator(denver)._elevation_fn()
    assert fn is not None

    # 2026-06-15, local Denver times expressed as UTC (MDT = UTC-6).
    before_sunrise = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)  # 04:00 local
    morning = datetime(2026, 6, 15, 13, 0, tzinfo=UTC)  # 07:00 local
    solar_noon = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)  # 13:00 local

    assert fn(before_sunrise) < 0
    assert 0 < fn(morning) < 25
    assert fn(solar_noon) > 60


async def test_real_elevation_caps_a_morning_walk(denver: HomeAssistant) -> None:
    """With a 15-degree cap the walk lands early, before the sun climbs out of range."""
    fn = _coordinator(denver)._elevation_fn()
    assert fn is not None

    now = datetime(2026, 6, 15, 11, 0, tzinfo=UTC)  # 05:00 local, before the window
    points = [
        ForecastPoint(when=now + timedelta(hours=h), temperature=60 + 2 * h, condition=None)
        for h in range(6)
    ]
    spec = ActivitySpec(
        start_hour=6,
        end_hour=9,
        use_temperature=False,
        min_temperature=60,
        max_temperature=75,
        use_elevation=True,
        max_elevation=15,
    )
    result = _best_time(points, 70, spec, now, fn)

    assert result.rolled_back == ()
    assert result.best_elevation <= 15
    # Cap holds across the buffer, not just at the chosen moment.
    assert fn(result.best_at + timedelta(minutes=20)) <= 15


async def test_elevation_pass_rolls_back_when_sun_is_too_high_all_window(
    denver: HomeAssistant,
) -> None:
    """A midday window in June never drops under 15 degrees, so the pass is dropped."""
    fn = _coordinator(denver)._elevation_fn()
    assert fn is not None

    now = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)  # 11:00 local
    points = [
        ForecastPoint(when=now + timedelta(hours=h), temperature=85 - h, condition=None)
        for h in range(4)
    ]
    spec = ActivitySpec(
        start_hour=11,
        end_hour=14,
        use_temperature=False,
        min_temperature=60,
        max_temperature=75,
        use_elevation=True,
        max_elevation=15,
    )
    result = _best_time(points, 70, spec, now, fn)

    assert result.rolled_back == ("elevation",)
    assert result.best_elevation > 15


async def test_coordinator_computes_activities_end_to_end(denver: HomeAssistant) -> None:
    """The subentry -> ActivitySpec -> staged passes path, with real sun elevation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="weather.home",
        data={CONF_WEATHER_ENTITY: "weather.home"},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_ACTIVITY,
                "title": "Morning walk",
                "unique_id": None,
                "data": {
                    "name": "Morning walk",
                    "start_hour": 6,
                    "end_hour": 9,
                    "use_temperature": True,
                    "min_temperature": 60,
                    "max_temperature": 75,
                    "use_elevation": True,
                    "max_elevation": 15,
                },
            }
        ],
    )
    entry.add_to_hass(denver)
    coordinator = WeatherPlusCoordinator(denver, entry)

    now = datetime(2026, 6, 15, 11, 0, tzinfo=UTC)  # 05:00 local
    points = [
        ForecastPoint(when=now + timedelta(hours=h), temperature=62 + 2 * h, condition=None)
        for h in range(6)
    ]
    results = coordinator._compute_activities(points, now)

    assert len(results) == 1
    result = next(iter(results.values()))
    assert result.best_at is not None
    assert 60 <= result.best_temperature <= 75
    assert result.best_elevation <= 15
    assert result.rolled_back == ()
