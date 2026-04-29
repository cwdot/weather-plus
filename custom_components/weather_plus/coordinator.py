"""Forecast coordinator: fetches hourly forecast and computes daily aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .conditions import ForecastPoint
from .const import (
    CONF_DAYTIME_HOUR,
    CONF_DAYTIME_MODE,
    CONF_MORNINGTIME_HOUR,
    CONF_NIGHTTIME_HOUR,
    CONF_SUN_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAYTIME_HOUR,
    DEFAULT_DAYTIME_MODE,
    DEFAULT_MORNINGTIME_HOUR,
    DEFAULT_NIGHTTIME_HOUR,
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
    morningtime_low: float | None
    daytime_high: float | None
    nighttime_low: float | None
    temperature_unit: str | None
    current_temperature: float | None = None
    forecast_points: list[ForecastPoint] = field(default_factory=list)
    morningtime_at: datetime | None = None
    daytime_at: datetime | None = None
    nighttime_at: datetime | None = None


@dataclass
class _CycleExtremes:
    cycle_start: datetime
    todays_high: float | None = None
    todays_low: float | None = None
    morningtime_low: float | None = None
    daytime_high: float | None = None
    nighttime_low: float | None = None


class WeatherPlusCoordinator(DataUpdateCoordinator[ForecastStats]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        self.weather_entity: str = data[CONF_WEATHER_ENTITY]
        self.daytime_mode: str = data.get(CONF_DAYTIME_MODE, DEFAULT_DAYTIME_MODE)
        self.sun_entity: str = data.get(CONF_SUN_ENTITY, DEFAULT_SUN_ENTITY)
        self.morningtime_hour: int = data.get(CONF_MORNINGTIME_HOUR, DEFAULT_MORNINGTIME_HOUR)
        self.daytime_hour: int = data.get(CONF_DAYTIME_HOUR, DEFAULT_DAYTIME_HOUR)
        self.nighttime_hour: int = data.get(CONF_NIGHTTIME_HOUR, DEFAULT_NIGHTTIME_HOUR)
        interval = data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        self.source_object_id = self.weather_entity.split(".", 1)[-1]
        state = hass.states.get(self.weather_entity)
        self.source_name = (state.name if state else None) or self.source_object_id

        self._extremes: _CycleExtremes | None = None

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
        dawn, noon, dusk = self._sun_anchors(now)
        m_at, d_at, n_at, next_m_at = _resolve_anchors(
            now,
            dawn,
            noon,
            dusk,
            self.morningtime_hour,
            self.daytime_hour,
            self.nighttime_hour,
        )
        fresh = _compute(
            forecast,
            m_at,
            d_at,
            n_at,
            next_m_at,
            unit,
            now,
            current_temperature=current,
        )
        fresh = replace(
            fresh,
            morningtime_at=m_at,
            daytime_at=d_at,
            nighttime_at=n_at,
        )
        return self._merge_extremes(fresh, now, m_at, d_at, n_at, next_m_at, current)

    def _merge_extremes(
        self,
        fresh: ForecastStats,
        now: datetime,
        m_at: datetime,
        d_at: datetime,
        n_at: datetime,
        next_m_at: datetime,
        current: float | None,
    ) -> ForecastStats:
        if self._extremes is None or self._extremes.cycle_start != m_at:
            self._extremes = _CycleExtremes(cycle_start=m_at)
        cache = self._extremes

        cache.todays_high = _max(cache.todays_high, fresh.todays_high)
        cache.todays_low = _min(cache.todays_low, fresh.todays_low)
        cache.morningtime_low = _min(cache.morningtime_low, fresh.morningtime_low)
        cache.daytime_high = _max(cache.daytime_high, fresh.daytime_high)
        cache.nighttime_low = _min(cache.nighttime_low, fresh.nighttime_low)

        if current is not None:
            cache.todays_high = _max(cache.todays_high, current)
            cache.todays_low = _min(cache.todays_low, current)
            bucket = _classify(now, m_at, d_at, n_at, next_m_at)
            if bucket == 0:
                cache.morningtime_low = _min(cache.morningtime_low, current)
            elif bucket == 1:
                cache.daytime_high = _max(cache.daytime_high, current)
            elif bucket == 2:
                cache.nighttime_low = _min(cache.nighttime_low, current)

        return replace(
            fresh,
            todays_high=cache.todays_high,
            todays_low=cache.todays_low,
            morningtime_low=cache.morningtime_low,
            daytime_high=cache.daytime_high,
            nighttime_low=cache.nighttime_low,
        )

    def reset_extremes(self) -> None:
        """Drop the carry-forward high/low cache; next refresh starts fresh."""
        self._extremes = None

    def _sun_anchors(
        self, now: datetime
    ) -> tuple[datetime | None, datetime | None, datetime | None]:
        if self.daytime_mode != MODE_SUN:
            return None, None, None
        state = self.hass.states.get(self.sun_entity)
        if state is None:
            _LOGGER.debug("sun entity %s unavailable; falling back to fixed hours", self.sun_entity)
            return None, None, None
        dawn = _today_event(state.attributes.get("next_dawn"), now)
        noon = _today_event(state.attributes.get("next_noon"), now)
        dusk = _today_event(state.attributes.get("next_dusk"), now)
        if dawn is None or noon is None or dusk is None:
            _LOGGER.debug(
                "sun entity %s missing next_dawn/next_noon/next_dusk; falling back to fixed hours",
                self.sun_entity,
            )
            return None, None, None
        return dawn, noon, dusk

    def _read_source_state(self) -> tuple[str | None, float | None]:
        state = self.hass.states.get(self.weather_entity)
        if state is None:
            return None, None
        unit = state.attributes.get("temperature_unit")
        raw = state.attributes.get("temperature")
        current = raw if isinstance(raw, int | float) else None
        return unit, current


def _today_event(raw: Any, now: datetime) -> datetime | None:
    """Resolve a sun next_* attribute to today's occurrence.

    The attribute is the *next* event; if its local date is after today, the
    matching event for today already happened, so step back 24h.
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


def _resolve_anchors(
    now: datetime,
    dawn: datetime | None,
    noon: datetime | None,
    dusk: datetime | None,
    morningtime_hour: int,
    daytime_hour: int,
    nighttime_hour: int,
) -> tuple[datetime, datetime, datetime, datetime]:
    """Return (morningtime, daytime, nighttime, next_morningtime) for the cycle containing `now`.

    A cycle runs morningtime → next morningtime (~24h). When `now` is before
    today's morningtime we're in yesterday's cycle, so the anchors slide back
    a day. When `now` is after, today's anchors are active and the cycle ends
    at tomorrow's morningtime.
    """
    if dawn is not None and noon is not None and dusk is not None:
        m_today, d_today, n_today = dawn, noon, dusk
    else:
        base = now.replace(minute=0, second=0, microsecond=0)
        m_today = base.replace(hour=morningtime_hour)
        d_today = base.replace(hour=daytime_hour)
        n_today = base.replace(hour=nighttime_hour)
    one_day = timedelta(days=1)
    if now >= m_today:
        return m_today, d_today, n_today, m_today + one_day
    return m_today - one_day, d_today - one_day, n_today - one_day, m_today


def _classify(
    point: datetime,
    m: datetime,
    d: datetime,
    n: datetime,
    next_m: datetime,
) -> int | None:
    """Return 0=morning, 1=daytime, 2=nighttime, or None if outside the cycle."""
    if point < m or point >= next_m:
        return None
    if point < d:
        return 0
    if point < n:
        return 1
    return 2


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


def _compute(
    forecast: list[dict[str, Any]],
    m_at: datetime,
    d_at: datetime,
    n_at: datetime,
    next_m_at: datetime,
    unit: str | None,
    now: datetime,
    current_temperature: float | None = None,
) -> ForecastStats:
    cycle, morningtime, daytime, nighttime = [], [], [], []
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
        bucket = _classify(parsed, m_at, d_at, n_at, next_m_at)
        if bucket is None:
            continue
        cycle.append(temp)
        if bucket == 0:
            morningtime.append(temp)
        elif bucket == 1:
            daytime.append(temp)
        else:
            nighttime.append(temp)

    return ForecastStats(
        todays_high=max(cycle) if cycle else None,
        todays_low=min(cycle) if cycle else None,
        morningtime_low=min(morningtime) if morningtime else None,
        daytime_high=max(daytime) if daytime else None,
        nighttime_low=min(nighttime) if nighttime else None,
        temperature_unit=unit,
        current_temperature=current_temperature,
        forecast_points=points,
    )
