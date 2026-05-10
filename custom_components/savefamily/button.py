from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
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
    async_add_entities(SaveFamilyRequestLocationButton(coordinator, did) for did in coordinator.data)


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
