"""Forecast coordinator: fetches hourly forecast and computes daily aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .conditions import ForecastPoint
from .const import (
    CONF_DAYTIME_END,
    CONF_DAYTIME_MODE,
    CONF_DAYTIME_START,
    CONF_SUN_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAYTIME_END,
    DEFAULT_DAYTIME_MODE,
    DEFAULT_DAYTIME_START,
    DEFAULT_SUN_ENTITY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MODE_SUN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ForecastStats:
    todays_high: float | None
    todays_low: float | None
    daytime_high: float | None
    daytime_low: float | None
    nighttime_high: float | None
    nighttime_low: float | None
    temperature_unit: str | None
    current_temperature: float | None = None
    forecast_points: list[ForecastPoint] = field(default_factory=list)
    daytime_at: datetime | None = None
    nighttime_at: datetime | None = None


@dataclass
class _DailyExtremes:
    day_date: date
    todays_high: float | None = None
    todays_low: float | None = None
    daytime_high: float | None = None
    daytime_low: float | None = None
    nighttime_high: float | None = None
    nighttime_low: float | None = None


class WeatherPlusCoordinator(DataUpdateCoordinator[ForecastStats]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        self.weather_entity: str = data[CONF_WEATHER_ENTITY]
        self.daytime_mode: str = data.get(CONF_DAYTIME_MODE, DEFAULT_DAYTIME_MODE)
        self.sun_entity: str = data.get(CONF_SUN_ENTITY, DEFAULT_SUN_ENTITY)
        self.daytime_start: int = data.get(CONF_DAYTIME_START, DEFAULT_DAYTIME_START)
        self.daytime_end: int = data.get(CONF_DAYTIME_END, DEFAULT_DAYTIME_END)
        interval = data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        self.source_object_id = self.weather_entity.split(".", 1)[-1]
        state = hass.states.get(self.weather_entity)
        self.source_name = (state.name if state else None) or self.source_object_id

        self._extremes: _DailyExtremes | None = None

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
        fresh = _compute(
            forecast,
            self.daytime_start,
            self.daytime_end,
            unit,
            now,
            sunrise=sunrise,
            sunset=sunset,
            current_temperature=current,
        )
        daytime_at, nighttime_at = _window_bounds(
            now, sunrise, sunset, self.daytime_start, self.daytime_end
        )
        fresh = replace(fresh, daytime_at=daytime_at, nighttime_at=nighttime_at)
        return self._merge_extremes(fresh, now, sunrise, sunset, current)

    def _merge_extremes(
        self,
        fresh: ForecastStats,
        now: datetime,
        sunrise: datetime | None,
        sunset: datetime | None,
        current: float | None,
    ) -> ForecastStats:
        today = now.date()
        if self._extremes is None or self._extremes.day_date != today:
            self._extremes = _DailyExtremes(day_date=today)
        cache = self._extremes

        cache.todays_high = _max(cache.todays_high, fresh.todays_high)
        cache.todays_low = _min(cache.todays_low, fresh.todays_low)
        cache.daytime_high = _max(cache.daytime_high, fresh.daytime_high)
        cache.daytime_low = _min(cache.daytime_low, fresh.daytime_low)
        cache.nighttime_high = _max(cache.nighttime_high, fresh.nighttime_high)
        cache.nighttime_low = _min(cache.nighttime_low, fresh.nighttime_low)

        # Fold the current observed temperature into the appropriate bucket.
        if current is not None:
            cache.todays_high = _max(cache.todays_high, current)
            cache.todays_low = _min(cache.todays_low, current)
            if _is_daytime(now, sunrise, sunset, self.daytime_start, self.daytime_end):
                cache.daytime_high = _max(cache.daytime_high, current)
                cache.daytime_low = _min(cache.daytime_low, current)
            else:
                cache.nighttime_high = _max(cache.nighttime_high, current)
                cache.nighttime_low = _min(cache.nighttime_low, current)

        return replace(
            fresh,
            todays_high=cache.todays_high,
            todays_low=cache.todays_low,
            daytime_high=cache.daytime_high,
            daytime_low=cache.daytime_low,
            nighttime_high=cache.nighttime_high,
            nighttime_low=cache.nighttime_low,
        )

    def reset_extremes(self) -> None:
        """Drop the carry-forward high/low cache; next refresh starts fresh."""
        self._extremes = None

    def _sun_window(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        if self.daytime_mode != MODE_SUN:
            return None, None
        state = self.hass.states.get(self.sun_entity)
        if state is None:
            _LOGGER.debug("sun entity %s unavailable; falling back to fixed hours", self.sun_entity)
            return None, None
        sunrise = _today_event(state.attributes.get("next_rising"), now)
        sunset = _today_event(state.attributes.get("next_setting"), now)
        if sunrise is None or sunset is None:
            _LOGGER.debug(
                "sun entity %s missing next_rising/next_setting; falling back to fixed hours",
                self.sun_entity,
            )
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


def _today_event(raw: Any, now: datetime) -> datetime | None:
    """Resolve a sun next_rising/next_setting attribute to today's occurrence.

    The attribute is the *next* event; if its local date is after today, the
    matching event for today already happened, so step back 24h as an estimate.
    """
    if not isinstance(raw, str):
        return None
    parsed = dt_util.parse_datetime(raw)
    if parsed is None:
        return None
    tz = now.tzinfo
    local = parsed.astimezone(tz) if tz is not None else dt_util.as_local(parsed)
    if local.date() == now.date():
        return parsed
    return parsed - timedelta(days=1)


def _max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _min(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _window_bounds(
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    daytime_start: int,
    daytime_end: int,
) -> tuple[datetime | None, datetime | None]:
    """Resolve when today's daytime and nighttime windows begin.

    In sun mode (sunrise/sunset present) returns those datetimes directly.
    In fixed mode, anchors today's local date at the configured hour and
    promotes back to the active timezone.
    """
    if sunrise is not None and sunset is not None:
        return sunrise, sunset
    base = now.replace(minute=0, second=0, microsecond=0)
    daytime_at = base.replace(hour=daytime_start)
    nighttime_at = (
        base.replace(hour=0) + timedelta(hours=daytime_end)
        if daytime_end == 24
        else base.replace(hour=daytime_end)
    )
    return daytime_at, nighttime_at


def _is_daytime(
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    daytime_start: int,
    daytime_end: int,
) -> bool:
    if sunrise is not None and sunset is not None:
        return sunrise <= now < sunset
    return daytime_start <= now.hour < daytime_end


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
    points: list[ForecastPoint] = []

    for point in forecast:
        raw_dt = point.get("datetime")
        if raw_dt is None:
            continue
        parsed = dt_util.parse_datetime(raw_dt)
        if parsed is None:
            continue

        temp = point.get("temperature")
        condition = point.get("condition")
        points.append(
            ForecastPoint(
                when=parsed,
                temperature=temp if isinstance(temp, int | float) else None,
                condition=condition if isinstance(condition, str) else None,
            )
        )

        if temp is None:
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
        todays_high=max(day) if day else None,
        todays_low=min(day) if day else None,
        daytime_high=max(daytime) if daytime else None,
        daytime_low=min(daytime) if daytime else None,
        nighttime_high=max(night) if night else None,
        nighttime_low=min(night) if night else None,
        temperature_unit=unit,
        current_temperature=current_temperature,
        forecast_points=points,
    )
