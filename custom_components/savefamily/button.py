from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .core.protocol import SaveFamilyError
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
        new_entities = [
            SaveFamilyRequestLocationButton(coordinator, did)
            for did in coordinator.data
            if did not in known_dids
        ]
        if new_entities:
            known_dids.update(e._did for e in new_entities)
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))
    _async_add_new_devices()


class SaveFamilyRequestLocationButton(SaveFamilyEntity, ButtonEntity):
    _attr_translation_key = "request_location"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_request_location"

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_request_location(self._did)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
