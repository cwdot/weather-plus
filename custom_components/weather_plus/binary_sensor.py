"""Binary sensors for forecast-driven weather conditions."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    coordinator: WeatherPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    if entry.options.get(CONF_ENABLE_CONDITIONS, DEFAULT_ENABLE_CONDITIONS):
        cold = float(entry.options.get(CONF_COLD_THRESHOLD, DEFAULT_COLD_THRESHOLD))
        hot = float(entry.options.get(CONF_HOT_THRESHOLD, DEFAULT_HOT_THRESHOLD))
        entities.extend(
            _ConditionBinarySensor(coordinator, entry, spec, cold, hot) for spec in CONDITION_SPECS
        )

    if coordinator.mower_precip_entity and coordinator.mower_temperature_entity:
        entities.append(_MowerBinarySensor(coordinator, entry))

    if entities:
        async_add_entities(entities)


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
            identifiers={(DOMAIN, entry.entry_id, "conditions")},
            name=f"{coordinator.source_object_id} Conditions",
            manufacturer="Weather Plus",
            model="Forecast conditions",
            via_device=(DOMAIN, entry.entry_id),
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


class _MowerBinarySensor(CoordinatorEntity[WeatherPlusCoordinator], BinarySensorEntity):
    """Wet/blocked when the moisture-balance model says the lawn is too wet to mow."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_name = "Mower"

    def __init__(
        self,
        coordinator: WeatherPlusCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_mower"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id, "mower")},
            name=f"{coordinator.source_object_id} Mower",
            manufacturer="Weather Plus",
            model="Mower readiness",
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.mower is not None

    @property
    def is_on(self) -> bool | None:
        mower = self.coordinator.data.mower
        return mower.is_wet if mower is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        mower = self.coordinator.data.mower
        if mower is None:
            return None
        return {"moisture_mm": round(mower.moisture_mm, 2)}
