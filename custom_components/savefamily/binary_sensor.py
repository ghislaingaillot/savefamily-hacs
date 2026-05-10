from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SaveFamilyConfigEntry
from .const import LOCATION_STALE_AFTER, ONLINE_THRESHOLD
from .entity import SaveFamilyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities = []
    for did in coordinator.data:
        entities.append(SaveFamilyLocationStaleSensor(coordinator, did))
        entities.append(SaveFamilyOnlineSensor(coordinator, did))
    async_add_entities(entities)


class SaveFamilyLocationStaleSensor(SaveFamilyEntity, BinarySensorEntity):
    _attr_translation_key = "location_stale"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_location_stale"

    @property
    def is_on(self) -> bool:
        last_fix = self.snapshot.last_fix
        if last_fix is None:
            return True
        return dt_util.utcnow() - last_fix > LOCATION_STALE_AFTER


class SaveFamilyOnlineSensor(SaveFamilyEntity, BinarySensorEntity):
    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_online"

    @property
    def is_on(self) -> bool:
        last_fix = self.snapshot.last_fix
        if last_fix is None:
            return False
        return dt_util.utcnow() - last_fix <= ONLINE_THRESHOLD
