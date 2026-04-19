"""Forecast coordinator: fetches hourly forecast and computes daily aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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

_LOGGER = logging.getLogger(__name__)


@dataclass
class ForecastStats:
    day_high: float | None
    day_low: float | None
    daytime_high: float | None
    daytime_low: float | None
    night_high: float | None
    night_low: float | None
    temperature_unit: str | None


class WeatherPlusCoordinator(DataUpdateCoordinator[ForecastStats]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        self.weather_entity: str = data[CONF_WEATHER_ENTITY]
        self.daytime_start: int = data.get(CONF_DAYTIME_START, DEFAULT_DAYTIME_START)
        self.daytime_end: int = data.get(CONF_DAYTIME_END, DEFAULT_DAYTIME_END)
        interval = data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{self.weather_entity}",
            update_interval=timedelta(minutes=interval),
        )

    async def _async_update_data(self) -> ForecastStats:
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": self.weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            raise UpdateFailed(f"forecast call failed: {err}") from err

        entity_data = (response or {}).get(self.weather_entity)
        if not entity_data:
            raise UpdateFailed(f"no forecast for {self.weather_entity}")

        forecast = entity_data.get("forecast") or []
        unit = self._read_temperature_unit()
        return _compute(forecast, self.daytime_start, self.daytime_end, unit, dt_util.now())

    def _read_temperature_unit(self) -> str | None:
        state = self.hass.states.get(self.weather_entity)
        if state is None:
            return None
        return state.attributes.get("temperature_unit")


def _compute(
    forecast: list[dict[str, Any]],
    daytime_start: int,
    daytime_end: int,
    unit: str | None,
    now: datetime,
) -> ForecastStats:
    tz = now.tzinfo
    today = now.date()
    day, daytime, night = [], [], []

    for point in forecast:
        raw_dt = point.get("datetime")
        temp = point.get("temperature")
        if raw_dt is None or temp is None:
            continue
        parsed = dt_util.parse_datetime(raw_dt)
        if parsed is None:
            continue
        local = parsed.astimezone(tz) if tz is not None else dt_util.as_local(parsed)
        if local.date() != today:
            continue
        day.append(temp)
        if daytime_start <= local.hour < daytime_end:
            daytime.append(temp)
        else:
            night.append(temp)

    return ForecastStats(
        day_high=max(day) if day else None,
        day_low=min(day) if day else None,
        daytime_high=max(daytime) if daytime else None,
        daytime_low=min(daytime) if daytime else None,
        night_high=max(night) if night else None,
        night_low=min(night) if night else None,
        temperature_unit=unit,
    )
