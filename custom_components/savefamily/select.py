from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .core.protocol import WORK_MODES, SaveFamilyError
from .entity import SaveFamilySettingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    settings_coordinator = entry.runtime_data.settings_coordinator
    entities_by_did: dict[str, SaveFamilyWorkModeSelect] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did, state in coordinator.data.items():
            if did not in entities_by_did:
                entity = SaveFamilyWorkModeSelect(settings_coordinator, state.watch, "work_mode")
                entities_by_did[did] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


class SaveFamilyWorkModeSelect(SaveFamilySettingsEntity, SelectEntity):
    """Position upload interval: real-time (1 min), normal (10 min) or power saving (1 h)."""

    _attr_translation_key = "work_mode"
    _attr_icon = "mdi:map-clock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(WORK_MODES)

    @property
    def current_option(self) -> str | None:
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.settings is None:
            return None
        return watch_settings.settings.work_mode

    async def async_select_option(self, option: str) -> None:
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.settings is None:
            raise HomeAssistantError("Watch settings are not loaded yet; try again later")
        try:
            await self.coordinator.client.async_set_work_mode(self._did, watch_settings.settings, option)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()
