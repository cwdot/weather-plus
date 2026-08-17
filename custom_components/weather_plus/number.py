"""Number platform: activity thresholds retunable without a config-flow round trip.

The temperature range that suits a walk in January is not the one that suits
July, so the range an activity searches is exposed as numbers an automation can
adjust seasonally. A value set here overrides the subentry's configured value
and survives restarts; the subentry value is the starting point.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ACTIVITY_NAME,
    CONF_MAX_ELEVATION,
    CONF_MAX_TEMPERATURE,
    CONF_MIN_TEMPERATURE,
    DEFAULT_MAX_ELEVATION,
    DEFAULT_MAX_TEMPERATURE,
    DEFAULT_MIN_TEMPERATURE,
    DOMAIN,
    SUBENTRY_TYPE_ACTIVITY,
)
from .coordinator import WeatherPlusCoordinator


@dataclass(frozen=True, kw_only=True)
class _NumberSpec:
    key: str
    name: str
    default: float
    native_min_value: float
    native_max_value: float
    native_step: float
    is_temperature: bool


# Temperature bounds are deliberately wide: the source entity may report either
# Fahrenheit or Celsius, and these are read in whatever unit it uses.
_SPECS: tuple[_NumberSpec, ...] = (
    _NumberSpec(
        key=CONF_MIN_TEMPERATURE,
        name="Minimum Temperature",
        default=DEFAULT_MIN_TEMPERATURE,
        native_min_value=-50,
        native_max_value=150,
        native_step=1,
        is_temperature=True,
    ),
    _NumberSpec(
        key=CONF_MAX_TEMPERATURE,
        name="Maximum Temperature",
        default=DEFAULT_MAX_TEMPERATURE,
        native_min_value=-50,
        native_max_value=150,
        native_step=1,
        is_temperature=True,
    ),
    _NumberSpec(
        key=CONF_MAX_ELEVATION,
        name="Maximum Sun Elevation",
        default=DEFAULT_MAX_ELEVATION,
        native_min_value=-90,
        native_max_value=90,
        native_step=1,
        is_temperature=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WeatherPlusCoordinator = hass.data[DOMAIN][entry.entry_id]

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_ACTIVITY:
            continue
        name = subentry.data[CONF_ACTIVITY_NAME]
        async_add_entities(
            [_ActivityThreshold(coordinator, entry, subentry_id, name, spec) for spec in _SPECS],
            config_subentry_id=subentry_id,
        )


class _ActivityThreshold(CoordinatorEntity[WeatherPlusCoordinator], RestoreNumber):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WeatherPlusCoordinator,
        entry: ConfigEntry,
        subentry_id: str,
        activity_name: str,
        spec: _NumberSpec,
    ) -> None:
        super().__init__(coordinator)
        self._subentry_id = subentry_id
        self._spec = spec
        self._attr_name = spec.name
        self._attr_unique_id = f"{subentry_id}_{spec.key}"
        self._attr_native_min_value = spec.native_min_value
        self._attr_native_max_value = spec.native_max_value
        self._attr_native_step = spec.native_step
        if spec.is_temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=activity_name,
            manufacturer="Weather Plus",
            model="Activity best time",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Reapply the retuned threshold, which the first refresh ran without.

        The coordinator refreshes during entry setup, before this entity exists,
        so a restored override has to force a recompute or the activity sensors
        would report the configured value until the next scheduled update.
        """
        await super().async_added_to_hass()
        restored = await self.async_get_last_number_data()
        if restored is None or restored.native_value is None:
            return
        if restored.native_value == self.native_value:
            return
        self.coordinator.set_activity_setting(
            self._subentry_id, self._spec.key, restored.native_value
        )
        await self.coordinator.async_refresh()

    @property
    def native_unit_of_measurement(self) -> str | None:
        if not self._spec.is_temperature:
            return DEGREE
        return self.coordinator.data.temperature_unit

    @property
    def native_value(self) -> float:
        return self.coordinator.activity_setting(
            self._subentry_id, self._spec.key, self._spec.default
        )

    async def async_set_native_value(self, value: float) -> None:
        # Refreshed rather than requested: async_request_refresh is debounced, so
        # a second threshold change inside the cooldown would silently not apply.
        self.coordinator.set_activity_setting(self._subentry_id, self._spec.key, value)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
