from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
    entities_by_did: dict[str, list[SaveFamilyEntity]] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                es = [
                    SaveFamilyLocationStaleSensor(coordinator, did),
                    SaveFamilyOnlineSensor(coordinator, did),
                ]
                entities_by_did[did] = es
                new_entities.extend(es)
        if new_entities:
            async_add_entities(new_entities)

        stale_dids = set(entities_by_did) - set(coordinator.data)
        if stale_dids:
            entity_reg = er.async_get(hass)
            for did in stale_dids:
                for entity in entities_by_did.pop(did):
                    if entity.entity_id:
                        entity_reg.async_remove(entity.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


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
