from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .const import TAKE_PHOTO_REFRESH_DELAY
from .core.protocol import (
    COMMAND_FIND_WATCH,
    COMMAND_POWER_OFF,
    COMMAND_RESTART,
    COMMAND_TAKE_PHOTO,
    SaveFamilyError,
)
from .entity import SaveFamilyEntity


@dataclass(frozen=True, kw_only=True)
class SaveFamilyCommandButtonDescription(ButtonEntityDescription):
    command: str
    with_model: bool = False
    after_press: Callable[[SaveFamilyConfigEntry], None] | None = None


COMMAND_BUTTONS = (
    SaveFamilyCommandButtonDescription(
        key="find_watch",
        translation_key="find_watch",
        icon="mdi:bell-ring",
        command=COMMAND_FIND_WATCH,
        with_model=True,
    ),
    SaveFamilyCommandButtonDescription(
        key="take_photo",
        translation_key="take_photo",
        icon="mdi:camera",
        command=COMMAND_TAKE_PHOTO,
        after_press=lambda entry: entry.runtime_data.settings_coordinator.async_schedule_delayed_refresh(
            TAKE_PHOTO_REFRESH_DELAY
        ),
    ),
    SaveFamilyCommandButtonDescription(
        key="restart",
        translation_key="restart",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
        command=COMMAND_RESTART,
        with_model=True,
    ),
    SaveFamilyCommandButtonDescription(
        key="power_off",
        translation_key="power_off",
        icon="mdi:power",
        entity_category=EntityCategory.CONFIG,
        # Powering off makes the watch unreachable until it is switched on by hand.
        entity_registry_enabled_default=False,
        command=COMMAND_POWER_OFF,
        with_model=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities_by_did: dict[str, list[ButtonEntity]] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                es: list[ButtonEntity] = [SaveFamilyRequestLocationButton(coordinator, did)]
                es.extend(SaveFamilyCommandButton(coordinator, did, entry, description) for description in COMMAND_BUTTONS)
                entities_by_did[did] = es
                new_entities.extend(es)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


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


class SaveFamilyCommandButton(SaveFamilyEntity, ButtonEntity):
    """Send one v2_sendOrder command to the watch."""

    entity_description: SaveFamilyCommandButtonDescription

    def __init__(
        self,
        coordinator,
        did: str,
        entry: SaveFamilyConfigEntry,
        description: SaveFamilyCommandButtonDescription,
    ) -> None:
        super().__init__(coordinator, did)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{did}_{description.key}"

    async def async_press(self) -> None:
        description = self.entity_description
        sent = await self.coordinator.async_send_command(
            self._did, description.command, with_model=description.with_model
        )
        if sent and description.after_press is not None:
            description.after_press(self._entry)
