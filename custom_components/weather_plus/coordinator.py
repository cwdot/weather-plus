"""Forecast coordinator: fetches hourly forecast and computes daily aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance as recorder_get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .conditions import ForecastPoint
from .const import (
    CONF_DAYTIME_HOUR,
    CONF_DAYTIME_MODE,
    CONF_MORNINGTIME_HOUR,
    CONF_MOWER_PRECIP_ENTITY,
    CONF_MOWER_TEMPERATURE_ENTITY,
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
from .mower import (
    DEFAULT_DRYING_RATES,
    DEFAULT_LOOKBACK,
    DEFAULT_PRECIP_RATE_MM_PER_HOUR,
    MowerForecastPoint,
    MowerReading,
    compute_average_precip_rate,
    compute_moisture_balance,
    predict_ready_time,
)

_LOGGER = logging.getLogger(__name__)

_STALE_FALLBACK_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class MowerState:
    moisture_mm: float
    is_wet: bool
    predicted_ready_at: datetime | None


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
    mower: MowerState | None = None


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
        self.mower_precip_entity: str | None = data.get(CONF_MOWER_PRECIP_ENTITY) or None
        self.mower_temperature_entity: str | None = data.get(CONF_MOWER_TEMPERATURE_ENTITY) or None
        interval = data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        self.source_object_id = self.weather_entity.split(".", 1)[-1]
        state = hass.states.get(self.weather_entity)
        self.source_name = (state.name if state else None) or self.source_object_id

        self._extremes: _CycleExtremes | None = None
        self._last_success: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{self.weather_entity}",
            update_interval=timedelta(minutes=interval),
        )

    async def _async_update_data(self) -> ForecastStats:
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

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": self.weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            entity_data = (response or {}).get(self.weather_entity)
            if not entity_data:
                raise RuntimeError(f"no forecast for {self.weather_entity}")
            forecast = entity_data.get("forecast") or []
        except Exception as err:
            base = self._fallback(err, now, m_at, d_at, n_at, next_m_at)
            mower = await self._compute_mower(now, base.forecast_points, base.temperature_unit)
            return replace(base, mower=mower)

        unit, current = self._read_source_state()
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
        merged = self._merge_extremes(fresh, now, m_at, d_at, n_at, next_m_at, current)
        mower = await self._compute_mower(now, merged.forecast_points, merged.temperature_unit)
        merged = replace(merged, mower=mower)
        self._last_success = now
        return merged

    async def _compute_mower(
        self,
        now: datetime,
        forecast_points: list[ForecastPoint],
        forecast_unit: str | None,
    ) -> MowerState | None:
        if not self.mower_precip_entity or not self.mower_temperature_entity:
            return None

        start = now - DEFAULT_LOOKBACK
        try:
            instance = recorder_get_instance(self.hass)
            history = await instance.async_add_executor_job(
                _fetch_history,
                self.hass,
                start,
                now,
                self.mower_precip_entity,
                self.mower_temperature_entity,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("mower history fetch failed: %s", err)
            return None

        precip_states = history.get(self.mower_precip_entity, [])
        temp_states = history.get(self.mower_temperature_entity, [])

        readings = _build_mower_readings(precip_states, temp_states)
        moisture = compute_moisture_balance(readings, DEFAULT_DRYING_RATES)
        is_wet = moisture > 0

        predicted_ready: datetime | None = None
        if is_wet:
            avg_rate = compute_average_precip_rate(readings, DEFAULT_PRECIP_RATE_MM_PER_HOUR)
            mower_forecast = _to_mower_forecast(forecast_points, forecast_unit)
            predicted_ready = predict_ready_time(
                moisture, mower_forecast, avg_rate, DEFAULT_DRYING_RATES
            )

        return MowerState(
            moisture_mm=moisture,
            is_wet=is_wet,
            predicted_ready_at=predicted_ready,
        )

    def _fallback(
        self,
        err: Exception,
        now: datetime,
        m_at: datetime,
        d_at: datetime,
        n_at: datetime,
        next_m_at: datetime,
    ) -> ForecastStats:
        """Serve cached aggregates + live current-temp when the forecast call fails."""
        if self._last_success is None:
            raise UpdateFailed(f"forecast unavailable, no cached data: {err}") from err
        age = now - self._last_success
        if age > _STALE_FALLBACK_AFTER:
            raise UpdateFailed(
                f"forecast unavailable for {age}, exceeding stale window: {err}"
            ) from err

        unit, current = self._read_source_state()
        if unit is None and self.data is not None:
            unit = self.data.temperature_unit
        _LOGGER.warning(
            "forecast for %s unavailable, serving cached aggregates (age=%s): %s",
            self.weather_entity,
            age,
            err,
        )
        empty = ForecastStats(
            todays_high=None,
            todays_low=None,
            morningtime_low=None,
            daytime_high=None,
            nighttime_low=None,
            temperature_unit=unit,
            current_temperature=current,
            forecast_points=[],
            morningtime_at=m_at,
            daytime_at=d_at,
            nighttime_at=n_at,
        )
        return self._merge_extremes(empty, now, m_at, d_at, n_at, next_m_at, current)

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


def _fetch_history(
    hass: HomeAssistant,
    start: datetime,
    end: datetime,
    *entity_ids: str,
) -> dict[str, list[State]]:
    """Recorder lookup for a few entity ids over a time window. Runs in executor."""
    merged: dict[str, list[State]] = {}
    for entity_id in entity_ids:
        result = state_changes_during_period(hass, start, end, entity_id=entity_id)
        merged.update(result)
    return merged


def _parse_state(raw: str | None) -> float | None:
    if raw is None or raw in ("unknown", "unavailable", "none", ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _to_fahrenheit(value: float, unit: str | None) -> float:
    if unit == UnitOfTemperature.FAHRENHEIT:
        return value
    if unit == UnitOfTemperature.CELSIUS or unit is None:
        return TemperatureConverter.convert(
            value, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
        )
    return TemperatureConverter.convert(value, unit, UnitOfTemperature.FAHRENHEIT)


def _build_mower_readings(
    precip_states: list[State],
    temp_states: list[State],
) -> list[MowerReading]:
    """Assemble MowerReadings using precip events as the spine.

    For each precip reading we carry forward the most recent temperature
    reading at-or-before its timestamp. Readings before the first temp sample
    are dropped — there is no temperature to apply drying with.
    """
    temp_history: list[tuple[datetime, float]] = []
    for s in temp_states:
        value = _parse_state(s.state)
        if value is None:
            continue
        unit = s.attributes.get("unit_of_measurement") if s.attributes else None
        temp_history.append((s.last_changed, _to_fahrenheit(value, unit)))
    temp_history.sort(key=lambda x: x[0])

    readings: list[MowerReading] = []
    temp_idx = -1
    for s in precip_states:
        precip = _parse_state(s.state)
        if precip is None:
            continue
        ts = s.last_changed
        while temp_idx + 1 < len(temp_history) and temp_history[temp_idx + 1][0] <= ts:
            temp_idx += 1
        if temp_idx < 0:
            continue
        readings.append(
            MowerReading(
                recorded_at=ts,
                temperature_f=temp_history[temp_idx][1],
                precip_today_mm=precip,
            )
        )
    return readings


def _to_mower_forecast(
    points: list[ForecastPoint],
    unit: str | None,
) -> list[MowerForecastPoint]:
    out: list[MowerForecastPoint] = []
    for p in points:
        if p.temperature is None:
            continue
        prob = p.precipitation_probability
        out.append(
            MowerForecastPoint(
                when=p.when,
                temperature_f=_to_fahrenheit(p.temperature, unit),
                precip_prob=float(prob) if prob is not None else 0.0,
            )
        )
    return out


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
        prob = point.get("precipitation_probability")
        points.append(
            ForecastPoint(
                when=parsed,
                temperature=temp if isinstance(temp, int | float) else None,
                condition=condition if isinstance(condition, str) else None,
                precipitation_probability=prob if isinstance(prob, int | float) else None,
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
