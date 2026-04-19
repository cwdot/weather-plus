"""Config flow for Weather Plus."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DAYTIME_END,
    CONF_DAYTIME_START,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAYTIME_END,
    DEFAULT_DAYTIME_START,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
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
        }
    )


class WeatherPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_DAYTIME_END] <= user_input[CONF_DAYTIME_START]:
                errors["base"] = "invalid_window"
            else:
                await self.async_set_unique_id(user_input[CONF_WEATHER_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Weather Plus ({user_input[CONF_WEATHER_ENTITY]})",
                    data={CONF_WEATHER_ENTITY: user_input[CONF_WEATHER_ENTITY]},
                    options={
                        CONF_DAYTIME_START: user_input[CONF_DAYTIME_START],
                        CONF_DAYTIME_END: user_input[CONF_DAYTIME_END],
                        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
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
            if user_input[CONF_DAYTIME_END] <= user_input[CONF_DAYTIME_START]:
                errors["base"] = "invalid_window"
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self._entry.options)),
            errors=errors,
        )
