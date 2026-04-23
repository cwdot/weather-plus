"""Binary sensors for forecast-driven weather conditions."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .conditions import CONDITION_SPECS, ConditionSpec, evaluate
from .const import (
    CONF_COLD_THRESHOLD,
    CONF_ENABLE_CONDITIONS,
    CONF_HOT_THRESHOLD,
    DEFAULT_COLD_THRESHOLD,
    DEFAULT_ENABLE_CONDITIONS,
    DEFAULT_HOT_THRESHOLD,
    DOMAIN,
)
from .coordinator import WeatherPlusCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not entry.options.get(CONF_ENABLE_CONDITIONS, DEFAULT_ENABLE_CONDITIONS):
        return

    coordinator: WeatherPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    cold = float(entry.options.get(CONF_COLD_THRESHOLD, DEFAULT_COLD_THRESHOLD))
    hot = float(entry.options.get(CONF_HOT_THRESHOLD, DEFAULT_HOT_THRESHOLD))

    async_add_entities(
        _ConditionBinarySensor(coordinator, entry, spec, cold, hot) for spec in CONDITION_SPECS
    )


class _ConditionBinarySensor(CoordinatorEntity[WeatherPlusCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WeatherPlusCoordinator,
        entry: ConfigEntry,
        spec: ConditionSpec,
        cold_threshold: float,
        hot_threshold: float,
    ) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._cold = cold_threshold
        self._hot = hot_threshold
        self._attr_unique_id = f"{entry.entry_id}_{spec.key}"
        self._attr_name = spec.name
        self._attr_device_class = spec.device_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.source_object_id,
            manufacturer="Weather Plus",
            model=f"Forecast aggregates for {coordinator.weather_entity}",
        )

    @property
    def is_on(self) -> bool:
        return evaluate(
            self._spec,
            self.coordinator.data.forecast_points,
            dt_util.utcnow(),
            self._cold,
            self._hot,
        )
