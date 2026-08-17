"""Integration tests for the runtime-adjustable activity thresholds.

These set up the real integration with a stub `weather.get_forecasts` service so
the number entities, the coordinator override store, and the staged passes are
exercised together — not in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weather_plus.const import (
    CONF_ENABLE_CONDITIONS,
    CONF_IDEAL_TEMPERATURE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_ACTIVITY,
)

_WEATHER = "weather.home"
_MIN = "number.morning_walk_minimum_temperature"
_MAX = "number.morning_walk_maximum_temperature"
_ELEVATION = "number.morning_walk_maximum_sun_elevation"
_BEST_TIME = "sensor.morning_walk_best_time"
_BEST_TEMP = "sensor.morning_walk_best_temperature"

# 05:00 UTC, before the 6-9 window. Temperature climbs 62 -> 82 across it.
_NOW = datetime(2026, 4, 18, 5, 0, tzinfo=UTC)
_HOURLY = [(6, 62), (7, 68), (8, 74), (9, 80), (10, 86)]


@pytest.fixture(autouse=True)
def _utc_default_tz():
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(original)


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(_NOW)


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    # The source entity reports °F; matching the unit system keeps the numbers'
    # display unit equal to their native unit, so no conversion muddies asserts.
    hass.config.units = US_CUSTOMARY_SYSTEM
    hass.states.async_set(
        _WEATHER,
        "sunny",
        {"temperature": 60, "temperature_unit": "°F", "friendly_name": "Home"},
    )

    def _forecasts(call) -> ServiceResponse:
        return {
            _WEATHER: {
                "forecast": [
                    {
                        "datetime": _NOW.replace(hour=hour).isoformat(),
                        "temperature": temp,
                        "condition": "sunny",
                    }
                    for hour, temp in _HOURLY
                ]
            }
        }

    hass.services.async_register(
        "weather",
        "get_forecasts",
        _forecasts,
        supports_response=SupportsResponse.ONLY,
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_WEATHER,
        data={CONF_WEATHER_ENTITY: _WEATHER},
        options={CONF_IDEAL_TEMPERATURE: 70, CONF_ENABLE_CONDITIONS: False},
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
                    "use_elevation": False,
                    "max_elevation": 15,
                },
            }
        ],
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def _set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id, "value": value},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_threshold_numbers_exist_with_configured_values(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    assert float(hass.states.get(_MIN).state) == 60
    assert float(hass.states.get(_MAX).state) == 75
    assert float(hass.states.get(_ELEVATION).state) == 15


async def test_summer_range_shifts_the_chosen_time_earlier(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Ideal is 70; a 60-75 range admits it, so the walk lands when it hits 70."""
    baseline = dt_util.parse_datetime(hass.states.get(_BEST_TIME).state)
    assert dt_util.as_local(baseline).hour == 7
    assert float(hass.states.get(_BEST_TEMP).state) == pytest.approx(70)

    # Summer: refuse anything over 66, forcing an earlier, cooler moment.
    await _set_number(hass, _MAX, 66)

    summer = dt_util.parse_datetime(hass.states.get(_BEST_TIME).state)
    assert summer < baseline
    assert float(hass.states.get(_BEST_TEMP).state) <= 66


async def test_winter_range_admits_colder_moments(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Dropping the floor lets the coldest end of the window qualify again."""
    await _set_number(hass, _MIN, 40)
    await _set_number(hass, _MAX, 64)

    assert float(hass.states.get(_BEST_TEMP).state) <= 64
    best_at = dt_util.parse_datetime(hass.states.get(_BEST_TIME).state)
    assert dt_util.as_local(best_at).hour == 6


async def test_impossible_range_rolls_back_the_temperature_pass(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """No moment can satisfy 100-110, so the pass is dropped and reported."""
    await _set_number(hass, _MIN, 100)
    await _set_number(hass, _MAX, 110)

    state = hass.states.get(_BEST_TIME)
    assert state.state not in ("unknown", "unavailable")
    assert state.attributes["rolled_back"] == ["temperature"]


async def test_number_value_survives_a_reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A seasonal retune must outlive a restart, or the automation has to re-run."""
    await _set_number(hass, _MAX, 66)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(_MAX).state) == 66
    assert float(hass.states.get(_BEST_TEMP).state) <= 66


async def test_elevation_number_drives_the_elevation_pass(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Enabling the pass and lowering the cap must change the search, not just the state."""
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "use_elevation": True}
    )
    await hass.async_block_till_done()

    await _set_number(hass, _ELEVATION, -90)  # sun is never this low; forces a rollback

    assert float(hass.states.get(_ELEVATION).state) == -90
    assert hass.states.get(_BEST_TIME).attributes["rolled_back"] == ["elevation"]


async def test_legacy_subentry_without_new_keys_still_works(hass: HomeAssistant) -> None:
    """Activities created before the staged passes existed carry none of the new keys.

    They must keep working on upgrade rather than raising KeyError, falling back
    to the defaults for anything the stored subentry does not define.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    hass.states.async_set(
        _WEATHER,
        "sunny",
        {"temperature": 60, "temperature_unit": "°F", "friendly_name": "Home"},
    )

    def _forecasts(call) -> ServiceResponse:
        return {
            _WEATHER: {
                "forecast": [
                    {
                        "datetime": _NOW.replace(hour=hour).isoformat(),
                        "temperature": temp,
                        "condition": "sunny",
                    }
                    for hour, temp in _HOURLY
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", _forecasts, supports_response=SupportsResponse.ONLY
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_WEATHER,
        data={CONF_WEATHER_ENTITY: _WEATHER},
        options={CONF_IDEAL_TEMPERATURE: 70, CONF_ENABLE_CONDITIONS: False},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_ACTIVITY,
                "title": "Morning walk",
                "unique_id": None,
                # Exactly the pre-upgrade shape: no pass settings at all.
                "data": {"name": "Morning walk", "start_hour": 6, "end_hour": 9},
            }
        ],
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_BEST_TIME).state not in ("unknown", "unavailable")
    assert float(hass.states.get(_MIN).state) == 60
    assert float(hass.states.get(_MAX).state) == 75
    assert float(hass.states.get(_ELEVATION).state) == 15
