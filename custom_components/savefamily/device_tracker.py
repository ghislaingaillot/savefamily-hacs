from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
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
    entities_by_did: dict[str, SaveFamilyTrackerEntity] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                entity = SaveFamilyTrackerEntity(coordinator, did)
                entities_by_did[did] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


class SaveFamilyTrackerEntity(SaveFamilyEntity, TrackerEntity):
    _attr_translation_key = "location"
    _attr_entity_category = None

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_location"

    @property
    def latitude(self) -> float | None:
        return self.snapshot.latitude

    @property
    def longitude(self) -> float | None:
        return self.snapshot.longitude

    @property
    def location_accuracy(self) -> int:
        return self.snapshot.accuracy or 0

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        attrs: dict[str, object] = {
            "did": self.snapshot.watch.did,
            "did_id": self.snapshot.watch.did_id,
            "model": self.snapshot.watch.model,
        }
        if self.snapshot.address:
            attrs["address"] = self.snapshot.address
        if self.snapshot.speed is not None:
            attrs["speed_kmh"] = self.snapshot.speed
        if self.snapshot.direction is not None:
            attrs["direction_degrees"] = self.snapshot.direction
        if self.snapshot.accuracy is not None:
            attrs["accuracy_m"] = self.snapshot.accuracy
        if self.snapshot.last_fix is not None:
            attrs["position_timestamp"] = self.snapshot.last_fix.isoformat()
        if self.snapshot.last_poll_status is not None:
            attrs["poll_status"] = self.snapshot.last_poll_status
        if self.snapshot.last_poll_message:
            attrs["poll_message"] = self.snapshot.last_poll_message
        return attrs
