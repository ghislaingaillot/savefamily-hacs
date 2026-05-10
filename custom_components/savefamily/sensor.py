from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .entity import SaveFamilyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    known_dids: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in known_dids:
                known_dids.add(did)
                new_entities.extend([
                    SaveFamilyBatterySensor(coordinator, did),
                    SaveFamilyLastFixSensor(coordinator, did),
                    SaveFamilyStepsSensor(coordinator, did),
                ])
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))
    _async_add_new_devices()


class SaveFamilyBatterySensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_battery"

    @property
    def native_value(self) -> int | None:
        return self.snapshot.battery


class SaveFamilyLastFixSensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "last_fix"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_last_fix"

    @property
    def native_value(self):
        return self.snapshot.last_fix


class SaveFamilyStepsSensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "steps"
    _attr_icon = "mdi:walk"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "steps"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_steps"

    @property
    def native_value(self) -> int | None:
        return self.snapshot.step_count
