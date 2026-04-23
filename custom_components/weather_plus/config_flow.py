"""Config flow for Weather Plus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COLD_THRESHOLD,
    CONF_DAYTIME_END,
    CONF_DAYTIME_MODE,
    CONF_DAYTIME_START,
    CONF_DUAL_UNIT,
    CONF_ENABLE_CONDITIONS,
    CONF_HOT_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DAYTIME_MODES,
    DEFAULT_COLD_THRESHOLD,
    DEFAULT_DAYTIME_END,
    DEFAULT_DAYTIME_MODE,
    DEFAULT_DAYTIME_START,
    DEFAULT_DUAL_UNIT,
    DEFAULT_ENABLE_CONDITIONS,
    DEFAULT_HOT_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MODE_FIXED,
)

_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=list(DAYTIME_MODES),
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key=CONF_DAYTIME_MODE,
    )
)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DAYTIME_MODE,
                default=defaults.get(CONF_DAYTIME_MODE, DEFAULT_DAYTIME_MODE),
            ): _MODE_SELECTOR,
            vol.Required(
                CONF_DAYTIME_START,
                default=defaults.get(CONF_DAYTIME_START, DEFAULT_DAYTIME_START),
            ): vol.All(int, vol.Range(min=0, max=23)),
            vol.Required(
                CONF_DAYTIME_END,
                default=defaults.get(CONF_DAYTIME_END, DEFAULT_DAYTIME_END),
            ): vol.All(int, vol.Range(min=1, max=24)),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Required(
                CONF_DUAL_UNIT,
                default=defaults.get(CONF_DUAL_UNIT, DEFAULT_DUAL_UNIT),
            ): bool,
            vol.Required(
                CONF_ENABLE_CONDITIONS,
                default=defaults.get(CONF_ENABLE_CONDITIONS, DEFAULT_ENABLE_CONDITIONS),
            ): bool,
            vol.Required(
                CONF_COLD_THRESHOLD,
                default=defaults.get(CONF_COLD_THRESHOLD, DEFAULT_COLD_THRESHOLD),
            ): vol.Coerce(float),
            vol.Required(
                CONF_HOT_THRESHOLD,
                default=defaults.get(CONF_HOT_THRESHOLD, DEFAULT_HOT_THRESHOLD),
            ): vol.Coerce(float),
        }
    )


def _validate(user_input: dict[str, Any]) -> str | None:
    if (
        user_input[CONF_DAYTIME_MODE] == MODE_FIXED
        and user_input[CONF_DAYTIME_END] <= user_input[CONF_DAYTIME_START]
    ):
        return "invalid_window"
    if user_input[CONF_HOT_THRESHOLD] <= user_input[CONF_COLD_THRESHOLD]:
        return "invalid_thresholds"
    return None


class WeatherPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            err = _validate(user_input)
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(user_input[CONF_WEATHER_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Weather Plus ({user_input[CONF_WEATHER_ENTITY]})",
                    data={CONF_WEATHER_ENTITY: user_input[CONF_WEATHER_ENTITY]},
                    options={
                        CONF_DAYTIME_MODE: user_input[CONF_DAYTIME_MODE],
                        CONF_DAYTIME_START: user_input[CONF_DAYTIME_START],
                        CONF_DAYTIME_END: user_input[CONF_DAYTIME_END],
                        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                        CONF_DUAL_UNIT: user_input[CONF_DUAL_UNIT],
                        CONF_ENABLE_CONDITIONS: user_input[CONF_ENABLE_CONDITIONS],
                        CONF_COLD_THRESHOLD: user_input[CONF_COLD_THRESHOLD],
                        CONF_HOT_THRESHOLD: user_input[CONF_HOT_THRESHOLD],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather"),
                ),
            }
        ).extend(_options_schema({}).schema)

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return WeatherPlusOptionsFlow(entry)


class WeatherPlusOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            err = _validate(user_input)
            if err:
                errors["base"] = err
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self._entry.options)),
            errors=errors,
        )
