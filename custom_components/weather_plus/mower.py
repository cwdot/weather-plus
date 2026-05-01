"""Mower-readiness moisture-balance model.

Ported from palantir's services/hassd/plugins/weather/internal/station/mower.go.

Tracks a moisture balance: precipitation (mm) accumulates, then evaporates at a
temperature-dependent drying rate. ``moisture <= 0`` means the lawn is dry
enough to mow. The PrecipToday signal is daily-cumulative and resets at
midnight; the delta logic handles the reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_LOOKBACK = timedelta(hours=72)
DEFAULT_PRECIP_RATE_MM_PER_HOUR = 2.5


@dataclass(frozen=True)
class DryingRate:
    min_temp_f: float
    max_temp_f: float
    rate_mm_per_hour: float


@dataclass(frozen=True)
class MowerReading:
    recorded_at: datetime
    temperature_f: float
    precip_today_mm: float


@dataclass(frozen=True)
class MowerForecastPoint:
    when: datetime
    temperature_f: float
    precip_prob: float  # 0-100


# Palantir's production drying rates (weather.yaml).
DEFAULT_DRYING_RATES: tuple[DryingRate, ...] = (
    DryingRate(32, 70, 1.0),
    DryingRate(70, 85, 2.0),
    DryingRate(85, 95, 2.5),
    DryingRate(95, 999, 3.0),
)


def drying_rate_for_temp(temp_f: float, rates: tuple[DryingRate, ...]) -> float:
    """Drying rate (mm/hour) at ``temp_f``; 0 outside any band (e.g. freezing)."""
    for r in rates:
        if r.min_temp_f <= temp_f < r.max_temp_f:
            return r.rate_mm_per_hour
    return 0.0


def _crosses_day_boundary(prev: datetime, curr: datetime) -> bool:
    p, c = prev.astimezone(), curr.astimezone()
    return p.toordinal() != c.toordinal()


def _precip_delta(prev: MowerReading, curr: MowerReading) -> float:
    """Delta accounting for the daily precip_today reset."""
    if curr.precip_today_mm >= prev.precip_today_mm:
        return curr.precip_today_mm - prev.precip_today_mm
    if _crosses_day_boundary(prev.recorded_at, curr.recorded_at):
        return curr.precip_today_mm
    return 0.0


def compute_moisture_balance(
    readings: list[MowerReading],
    drying_rates: tuple[DryingRate, ...],
) -> float:
    """Walk readings chronologically, returning final moisture (mm)."""
    if len(readings) < 2:
        return 0.0
    moisture = 0.0
    for i in range(1, len(readings)):
        prev, curr = readings[i - 1], readings[i]
        moisture += _precip_delta(prev, curr)
        hours = (curr.recorded_at - prev.recorded_at).total_seconds() / 3600.0
        moisture -= drying_rate_for_temp(curr.temperature_f, drying_rates) * hours
        if moisture < 0:
            moisture = 0.0
    return moisture


def compute_average_precip_rate(
    readings: list[MowerReading],
    default_rate: float,
) -> float:
    """Average precip rate (mm/hr) during periods that were actively raining."""
    if len(readings) < 2:
        return default_rate
    total_precip = 0.0
    rainy_hours = 0.0
    for i in range(1, len(readings)):
        delta = _precip_delta(readings[i - 1], readings[i])
        if delta > 0:
            total_precip += delta
            span = readings[i].recorded_at - readings[i - 1].recorded_at
            elapsed = span.total_seconds() / 3600.0
            if elapsed > 0:
                rainy_hours += elapsed
    if rainy_hours == 0 or total_precip == 0:
        return default_rate
    return total_precip / rainy_hours


def predict_ready_time(
    current_moisture: float,
    forecasts: list[MowerForecastPoint],
    avg_precip_rate: float,
    drying_rates: tuple[DryingRate, ...],
) -> datetime | None:
    """First forecast hour where moisture would hit zero, or None."""
    if current_moisture <= 0 or len(forecasts) < 2:
        return None
    moisture = current_moisture
    for i in range(1, len(forecasts)):
        prev, curr = forecasts[i - 1], forecasts[i]
        elapsed = (curr.when - prev.when).total_seconds() / 3600.0
        if elapsed <= 0:
            continue
        moisture += avg_precip_rate * (curr.precip_prob / 100.0) * elapsed
        moisture -= drying_rate_for_temp(curr.temperature_f, drying_rates) * elapsed
        if moisture <= 0:
            return curr.when
    return None
