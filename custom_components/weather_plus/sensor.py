"""Sensor platform exposing forecast aggregates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ForecastStats, WeatherPlusCoordinator


@dataclass(frozen=True, kw_only=True)
class _Spec(SensorEntityDescription):
    value_fn: Callable[[ForecastStats], float | None]


SENSORS: tuple[_Spec, ...] = (
    _Spec(key="day_high", name="Day High", value_fn=lambda s: s.day_high),
    _Spec(key="day_low", name="Day Low", value_fn=lambda s: s.day_low),
    _Spec(key="daytime_high", name="Daytime High", value_fn=lambda s: s.daytime_high),
    _Spec(key="daytime_low", name="Daytime Low", value_fn=lambda s: s.daytime_low),
    _Spec(key="night_high", name="Night High", value_fn=lambda s: s.night_high),
    _Spec(key="night_low", name="Night Low", value_fn=lambda s: s.night_low),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WeatherPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_ForecastSensor(coordinator, entry, spec) for spec in SENSORS)


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
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.coordinator.data.temperature_unit
