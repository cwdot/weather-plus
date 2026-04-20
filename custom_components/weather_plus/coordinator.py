"""Forecast coordinator: fetches hourly forecast and computes daily aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAYTIME_END,
    CONF_DAYTIME_MODE,
    CONF_DAYTIME_START,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAYTIME_END,
    DEFAULT_DAYTIME_MODE,
    DEFAULT_DAYTIME_START,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MODE_SUN,
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
    current_temperature: float | None = None


class WeatherPlusCoordinator(DataUpdateCoordinator[ForecastStats]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        self.weather_entity: str = data[CONF_WEATHER_ENTITY]
        self.daytime_mode: str = data.get(CONF_DAYTIME_MODE, DEFAULT_DAYTIME_MODE)
        self.daytime_start: int = data.get(CONF_DAYTIME_START, DEFAULT_DAYTIME_START)
        self.daytime_end: int = data.get(CONF_DAYTIME_END, DEFAULT_DAYTIME_END)
        interval = data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        self.source_object_id = self.weather_entity.split(".", 1)[-1]
        state = hass.states.get(self.weather_entity)
        self.source_name = (state.name if state else None) or self.source_object_id

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
        unit, current = self._read_source_state()
        now = dt_util.now()
        sunrise, sunset = self._sun_window(now)
        return _compute(
            forecast,
            self.daytime_start,
            self.daytime_end,
            unit,
            now,
            sunrise=sunrise,
            sunset=sunset,
            current_temperature=current,
        )

    def _sun_window(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        if self.daytime_mode != MODE_SUN:
            return None, None
        today = now.date()
        sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, today)
        sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, today)
        if sunrise is None or sunset is None:
            _LOGGER.debug("sun events unavailable for %s; falling back to fixed hours", today)
            return None, None
        return sunrise, sunset

    def _read_source_state(self) -> tuple[str | None, float | None]:
        state = self.hass.states.get(self.weather_entity)
        if state is None:
            return None, None
        unit = state.attributes.get("temperature_unit")
        raw = state.attributes.get("temperature")
        current = raw if isinstance(raw, int | float) else None
        return unit, current


def _compute(
    forecast: list[dict[str, Any]],
    daytime_start: int,
    daytime_end: int,
    unit: str | None,
    now: datetime,
    sunrise: datetime | None = None,
    sunset: datetime | None = None,
    current_temperature: float | None = None,
) -> ForecastStats:
    tz = now.tzinfo
    today = now.date()
    use_sun = sunrise is not None and sunset is not None
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
        if use_sun:
            is_daytime = sunrise <= parsed < sunset
        else:
            is_daytime = daytime_start <= local.hour < daytime_end
        if is_daytime:
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
        current_temperature=current_temperature,
    )
