"""Sensor platform exposing forecast aggregates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import CONF_DUAL_UNIT, DEFAULT_DUAL_UNIT, DOMAIN
from .coordinator import ForecastStats, WeatherPlusCoordinator

_DUAL_UNITS: tuple[str, ...] = (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS)
_UNIT_SUFFIX: dict[str, str] = {
    UnitOfTemperature.FAHRENHEIT: "f",
    UnitOfTemperature.CELSIUS: "c",
}


@dataclass(frozen=True, kw_only=True)
class _Spec(SensorEntityDescription):
    value_fn: Callable[[ForecastStats], float | None]


@dataclass(frozen=True, kw_only=True)
class _TimestampSpec(SensorEntityDescription):
    value_fn: Callable[[ForecastStats], datetime | None]


_FORECAST_SPECS: tuple[_Spec, ...] = (
    _Spec(key="todays_high", name="Today's High", value_fn=lambda s: s.todays_high),
    _Spec(key="todays_low", name="Today's Low", value_fn=lambda s: s.todays_low),
    _Spec(key="morningtime_low", name="Morningtime Low", value_fn=lambda s: s.morningtime_low),
    _Spec(key="daytime_high", name="Daytime High", value_fn=lambda s: s.daytime_high),
    _Spec(key="nighttime_low", name="Nighttime Low", value_fn=lambda s: s.nighttime_low),
)

_CURRENT_SPEC = _Spec(
    key="current_temperature",
    name="Current Temperature",
    value_fn=lambda s: s.current_temperature,
)

_TIMESTAMP_SPECS: tuple[_TimestampSpec, ...] = (
    _TimestampSpec(key="morningtime_at", name="Morningtime", value_fn=lambda s: s.morningtime_at),
    _TimestampSpec(key="daytime_at", name="Daytime", value_fn=lambda s: s.daytime_at),
    _TimestampSpec(key="nighttime_at", name="Nighttime", value_fn=lambda s: s.nighttime_at),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WeatherPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    dual = entry.options.get(CONF_DUAL_UNIT, DEFAULT_DUAL_UNIT)

    sensors: list[SensorEntity] = []
    for spec in _FORECAST_SPECS:
        if dual:
            sensors.extend(
                _ForecastSensor(coordinator, entry, spec, target_unit=u) for u in _DUAL_UNITS
            )
        else:
            sensors.append(_ForecastSensor(coordinator, entry, spec))

    if dual:
        source_unit = coordinator.data.temperature_unit
        other_unit = next((u for u in _DUAL_UNITS if u != source_unit), None)
        if other_unit is not None:
            sensors.append(
                _ForecastSensor(coordinator, entry, _CURRENT_SPEC, target_unit=other_unit)
            )

    sensors.extend(_TimestampSensor(coordinator, entry, spec) for spec in _TIMESTAMP_SPECS)

    async_add_entities(sensors)


class _ForecastSensor(CoordinatorEntity[WeatherPlusCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    entity_description: _Spec

    def __init__(
        self,
        coordinator: WeatherPlusCoordinator,
        entry: ConfigEntry,
        spec: _Spec,
        target_unit: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._target_unit = target_unit

        if target_unit is None:
            self.entity_description = spec
            self._attr_unique_id = f"{entry.entry_id}_{spec.key}"
        else:
            suffix = _UNIT_SUFFIX[target_unit]
            self.entity_description = _Spec(
                key=f"{spec.key}_{suffix}",
                name=f"{spec.name} ({target_unit})",
                value_fn=spec.value_fn,
            )
            self._attr_unique_id = f"{entry.entry_id}_{spec.key}_{suffix}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.source_object_id,
            manufacturer="Weather Plus",
            model=f"Forecast aggregates for {coordinator.weather_entity}",
        )

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.coordinator.data)
        if value is None:
            return None
        if self._target_unit is None:
            return value
        source_unit = self.coordinator.data.temperature_unit
        if source_unit is None or source_unit == self._target_unit:
            return value
        return TemperatureConverter.convert(value, source_unit, self._target_unit)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._target_unit or self.coordinator.data.temperature_unit


class _TimestampSensor(CoordinatorEntity[WeatherPlusCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    entity_description: _TimestampSpec

    def __init__(
        self,
        coordinator: WeatherPlusCoordinator,
        entry: ConfigEntry,
        spec: _TimestampSpec,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = spec
        self._attr_unique_id = f"{entry.entry_id}_{spec.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.source_object_id,
            manufacturer="Weather Plus",
            model=f"Forecast aggregates for {coordinator.weather_entity}",
        )

    @property
    def native_value(self) -> datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)
