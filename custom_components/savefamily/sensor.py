from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .entity import SaveFamilyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities = []
    for did in coordinator.data:
        entities.append(SaveFamilyBatterySensor(coordinator, did))
        entities.append(SaveFamilyLastFixSensor(coordinator, did))
        entities.append(SaveFamilyStepsSensor(coordinator, did))
    async_add_entities(entities)


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
