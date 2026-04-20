"""Sensor-level tests focused on dual-unit conversion."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import UnitOfTemperature

from custom_components.weather_plus.coordinator import ForecastStats
from custom_components.weather_plus.sensor import _FORECAST_SPECS, _ForecastSensor


def _build(target_unit: str | None, source_unit: str, value: float | None = 100.0):
    """Construct a sensor without invoking CoordinatorEntity.__init__."""
    coordinator = SimpleNamespace(
        data=ForecastStats(
            day_high=value,
            day_low=None,
            daytime_high=None,
            daytime_low=None,
            night_high=None,
            night_low=None,
            temperature_unit=source_unit,
        ),
        source_object_id="home",
        weather_entity="weather.home",
    )
    sensor = _ForecastSensor.__new__(_ForecastSensor)
    sensor.coordinator = coordinator
    sensor._target_unit = target_unit
    sensor.entity_description = _FORECAST_SPECS[0]  # day_high
    return sensor


def test_native_mode_returns_source_value_unchanged():
    sensor = _build(target_unit=None, source_unit=UnitOfTemperature.FAHRENHEIT, value=72)
    assert sensor.native_value == 72
    assert sensor.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT


def test_dual_mode_no_conversion_when_units_match():
    sensor = _build(
        target_unit=UnitOfTemperature.FAHRENHEIT,
        source_unit=UnitOfTemperature.FAHRENHEIT,
        value=72,
    )
    assert sensor.native_value == 72
    assert sensor.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT


def test_dual_mode_fahrenheit_to_celsius():
    sensor = _build(
        target_unit=UnitOfTemperature.CELSIUS,
        source_unit=UnitOfTemperature.FAHRENHEIT,
        value=32,
    )
    assert sensor.native_value == 0.0
    assert sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS


def test_dual_mode_celsius_to_fahrenheit():
    sensor = _build(
        target_unit=UnitOfTemperature.FAHRENHEIT,
        source_unit=UnitOfTemperature.CELSIUS,
        value=100,
    )
    assert sensor.native_value == 212.0


def test_none_value_skips_conversion():
    sensor = _build(
        target_unit=UnitOfTemperature.CELSIUS,
        source_unit=UnitOfTemperature.FAHRENHEIT,
        value=None,
    )
    assert sensor.native_value is None


def test_unknown_source_unit_returns_value_as_is():
    sensor = _build(target_unit=UnitOfTemperature.CELSIUS, source_unit=None, value=42)
    assert sensor.native_value == 42
