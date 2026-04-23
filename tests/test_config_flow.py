"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weather_plus.const import (
    CONF_COLD_THRESHOLD,
    CONF_DAYTIME_END,
    CONF_DAYTIME_MODE,
    CONF_DAYTIME_START,
    CONF_DUAL_UNIT,
    CONF_ENABLE_CONDITIONS,
    CONF_HOT_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_COLD_THRESHOLD,
    DEFAULT_ENABLE_CONDITIONS,
    DEFAULT_HOT_THRESHOLD,
    DOMAIN,
    MODE_FIXED,
    MODE_SUN,
)

_VALID = {
    CONF_WEATHER_ENTITY: "weather.home",
    CONF_DAYTIME_MODE: MODE_FIXED,
    CONF_DAYTIME_START: 6,
    CONF_DAYTIME_END: 20,
    CONF_UPDATE_INTERVAL: 30,
    CONF_DUAL_UNIT: False,
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch("custom_components.weather_plus.async_setup_entry", return_value=True):
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], _VALID)
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"] == {CONF_WEATHER_ENTITY: "weather.home"}
    assert result2["options"] == {
        CONF_DAYTIME_MODE: MODE_FIXED,
        CONF_DAYTIME_START: 6,
        CONF_DAYTIME_END: 20,
        CONF_UPDATE_INTERVAL: 30,
        CONF_DUAL_UNIT: False,
        CONF_ENABLE_CONDITIONS: DEFAULT_ENABLE_CONDITIONS,
        CONF_COLD_THRESHOLD: DEFAULT_COLD_THRESHOLD,
        CONF_HOT_THRESHOLD: DEFAULT_HOT_THRESHOLD,
    }


async def test_user_flow_rejects_invalid_window(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**_VALID, CONF_DAYTIME_START: 20, CONF_DAYTIME_END: 6},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_window"}


async def test_duplicate_entity_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="weather.home",
        data={CONF_WEATHER_ENTITY: "weather.home"},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(result["flow_id"], _VALID)
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_options_flow_rejects_invalid_window(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="weather.home",
        data={CONF_WEATHER_ENTITY: "weather.home"},
        options={
            CONF_DAYTIME_MODE: MODE_FIXED,
            CONF_DAYTIME_START: 6,
            CONF_DAYTIME_END: 20,
            CONF_UPDATE_INTERVAL: 30,
            CONF_DUAL_UNIT: False,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DAYTIME_MODE: MODE_FIXED,
            CONF_DAYTIME_START: 20,
            CONF_DAYTIME_END: 6,
            CONF_UPDATE_INTERVAL: 30,
            CONF_DUAL_UNIT: False,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_window"}


async def test_sun_mode_ignores_hour_order(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.weather_plus.async_setup_entry", return_value=True):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITY: "weather.home",
                CONF_DAYTIME_MODE: MODE_SUN,
                CONF_DAYTIME_START: 20,
                CONF_DAYTIME_END: 6,
                CONF_UPDATE_INTERVAL: 30,
                CONF_DUAL_UNIT: True,
            },
        )
        await hass.async_block_till_done()
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["options"][CONF_DAYTIME_MODE] == MODE_SUN
