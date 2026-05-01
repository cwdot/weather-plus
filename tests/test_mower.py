"""Mower moisture-balance tests, ported from palantir's mower_test.go."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.weather_plus.mower import (
    DryingRate,
    MowerForecastPoint,
    MowerReading,
    compute_average_precip_rate,
    compute_moisture_balance,
    drying_rate_for_temp,
    predict_ready_time,
)

# Palantir's test rates (slightly different from production: 1.5 / 2.0 in upper bands).
TEST_RATES: tuple[DryingRate, ...] = (
    DryingRate(32, 70, 1.0),
    DryingRate(70, 85, 1.5),
    DryingRate(85, 999, 2.0),
)

_BASE = datetime(2026, 4, 1, 12, 0)


def _r(hours: float, temp_f: float, precip_mm: float) -> MowerReading:
    return MowerReading(
        recorded_at=_BASE + timedelta(hours=hours),
        temperature_f=temp_f,
        precip_today_mm=precip_mm,
    )


def _f(hours: int, temp_f: float, precip_prob: float) -> MowerForecastPoint:
    return MowerForecastPoint(
        when=_BASE + timedelta(hours=hours),
        temperature_f=temp_f,
        precip_prob=precip_prob,
    )


class TestMoistureBalance:
    def test_empty(self):
        assert compute_moisture_balance([], TEST_RATES) == 0.0

    def test_single_reading(self):
        assert compute_moisture_balance([_r(0, 75, 0)], TEST_RATES) == 0.0

    def test_no_precip_warm_dry(self):
        readings = [_r(0, 75, 0), _r(1, 75, 0), _r(2, 75, 0)]
        assert compute_moisture_balance(readings, TEST_RATES) == 0.0

    def test_rain_then_drying_warm(self):
        # 5mm rain over 1h, 2 total drying hours @ 1.5mm/hr → 5 -1.5 -1.5 = 2.0
        readings = [_r(0, 75, 0), _r(1, 75, 5), _r(2, 75, 5)]
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 2.0) < 0.01

    def test_rain_then_drying_hot(self):
        # 5mm rain @ 90°F → 2 mm/hr × 2h drying → 5 -2 -2 = 1.0
        readings = [_r(0, 90, 0), _r(1, 90, 5), _r(2, 90, 5)]
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 1.0) < 0.01

    def test_rain_then_drying_cold(self):
        # 5mm rain @ 50°F → 1 mm/hr × 2h drying → 5 -1 -1 = 3.0
        readings = [_r(0, 50, 0), _r(1, 50, 5), _r(2, 50, 5)]
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 3.0) < 0.01

    def test_rain_then_freezing(self):
        # 5mm rain, freezing temps → 0 mm/hr drying, stays 5.0
        readings = [_r(0, 25, 0), _r(1, 25, 5), _r(2, 25, 5), _r(3, 25, 5)]
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 5.0) < 0.01

    def test_rain_dries_completely(self):
        # 3mm rain @ 90°F (2 mm/hr) for 2h after rain → -4mm clamped to 0
        readings = [_r(0, 90, 0), _r(1, 90, 3), _r(3, 90, 3)]
        assert compute_moisture_balance(readings, TEST_RATES) == 0.0

    def test_multiple_rain_events(self):
        # +3 -1.5 = 1.5 | -4.5 → 0 | +2 -1.5 = 0.5 | -1.5 → 0
        readings = [_r(0, 75, 0), _r(1, 75, 3), _r(4, 75, 3), _r(5, 75, 5), _r(6, 75, 5)]
        assert compute_moisture_balance(readings, TEST_RATES) == 0.0

    def test_heavy_rain_slow_drying(self):
        # 25mm @ 50°F (1 mm/hr) for 11h → 25 -1 -10 = 14
        readings = [_r(0, 50, 0), _r(1, 50, 25), _r(11, 50, 25)]
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 14.0) < 0.01

    def test_midnight_reset(self):
        # precip_today resets at midnight; delta logic should treat post-reset value as new precip.
        midnight = datetime(2026, 4, 1, 23, 50)
        readings = [
            MowerReading(recorded_at=midnight, temperature_f=75, precip_today_mm=10),
            MowerReading(
                recorded_at=midnight + timedelta(minutes=20),
                temperature_f=75,
                precip_today_mm=0.5,
            ),
        ]
        # delta = 0.5, drying = 1.5 * 20/60 = 0.5 → 0.0
        assert abs(compute_moisture_balance(readings, TEST_RATES) - 0.0) < 0.01

    def test_temperature_transition(self):
        # +10 -1 = 9 | -2 = 7 | -3 = 4 | -4 = 0
        readings = [
            _r(0, 50, 0),
            _r(1, 50, 10),
            _r(3, 50, 10),
            _r(5, 75, 10),
            _r(7, 90, 10),
        ]
        assert compute_moisture_balance(readings, TEST_RATES) == 0.0


class TestAveragePrecipRate:
    def test_empty(self):
        assert compute_average_precip_rate([], 2.5) == 2.5

    def test_no_rain(self):
        readings = [_r(0, 75, 0), _r(1, 75, 0), _r(2, 75, 0)]
        assert compute_average_precip_rate(readings, 2.5) == 2.5

    def test_steady_rain(self):
        # 10mm over 2 rainy hours → 5 mm/hr
        readings = [_r(0, 75, 0), _r(1, 75, 5), _r(2, 75, 10)]
        assert abs(compute_average_precip_rate(readings, 2.5) - 5.0) < 0.01

    def test_rain_then_dry(self):
        # 6mm in 1 rainy hour, then 2 dry hours → 6 mm/hr
        readings = [_r(0, 75, 0), _r(1, 75, 6), _r(2, 75, 6), _r(3, 75, 6)]
        assert abs(compute_average_precip_rate(readings, 2.5) - 6.0) < 0.01


class TestPredictReadyTime:
    def test_already_dry(self):
        forecasts = [_f(0, 75, 0), _f(1, 75, 0)]
        assert predict_ready_time(0, forecasts, 2.5, TEST_RATES) is None

    def test_dries_with_no_rain(self):
        # 3mm @ 1.5 mm/hr → dry after 2h
        forecasts = [_f(i, 75, 0) for i in range(4)]
        assert predict_ready_time(3.0, forecasts, 2.5, TEST_RATES) == _BASE + timedelta(hours=2)

    def test_dries_slower_with_rain(self):
        # 50% × 2 mm/hr − 1.5 mm/hr = -0.5/hr → 6h
        forecasts = [_f(i, 75, 50) for i in range(8)]
        assert predict_ready_time(3.0, forecasts, 2.0, TEST_RATES) == _BASE + timedelta(hours=6)

    def test_never_dries_heavy_rain(self):
        forecasts = [_f(i, 75, 100) for i in range(3)]
        assert predict_ready_time(5.0, forecasts, 5.0, TEST_RATES) is None

    def test_freezing_no_drying(self):
        forecasts = [_f(i, 25, 0) for i in range(3)]
        assert predict_ready_time(2.0, forecasts, 2.5, TEST_RATES) is None

    def test_warm_up_after_freeze(self):
        # i=2 (75°F): 2 - 1.5 = 0.5 | i=3: 0.5 - 1.5 → dry
        forecasts = [_f(0, 25, 0), _f(1, 25, 0), _f(2, 75, 0), _f(3, 75, 0), _f(4, 75, 0)]
        assert predict_ready_time(2.0, forecasts, 2.5, TEST_RATES) == _BASE + timedelta(hours=3)

    def test_insufficient_forecasts(self):
        assert predict_ready_time(5.0, [_f(0, 75, 0)], 2.5, TEST_RATES) is None


class TestDryingRateForTemp:
    def test_below_freezing(self):
        assert drying_rate_for_temp(20, TEST_RATES) == 0.0
        assert drying_rate_for_temp(31.9, TEST_RATES) == 0.0

    def test_cool_range(self):
        assert drying_rate_for_temp(32, TEST_RATES) == 1.0
        assert drying_rate_for_temp(50, TEST_RATES) == 1.0
        assert drying_rate_for_temp(69.9, TEST_RATES) == 1.0

    def test_warm_range(self):
        assert drying_rate_for_temp(70, TEST_RATES) == 1.5
        assert drying_rate_for_temp(77, TEST_RATES) == 1.5
        assert drying_rate_for_temp(84.9, TEST_RATES) == 1.5

    def test_hot_range(self):
        assert drying_rate_for_temp(85, TEST_RATES) == 2.0
        assert drying_rate_for_temp(100, TEST_RATES) == 2.0
