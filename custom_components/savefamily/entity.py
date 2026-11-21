from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SIGNAL_WATCH_HANDED_OVER, SIGNAL_WATCH_REMOVED
from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilySettingsCoordinator
from .core.protocol import SaveFamilyWatch, SaveFamilyWatchSettings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


@callback
def async_forget_removed_watches(hass: HomeAssistant, entry: ConfigEntry, entities_by_did: dict[str, Any]) -> None:
    """Let a platform add a watch's entities again if it comes back after its device was removed.

    When another entry takes the watch over, also remove its entities but keep their registry
    entries, so that they move to that entry with their entity ids and settings.
    """

    @callback
    def _async_forget(did: str) -> None:
        # The registry removes the entities of a removed device.
        entities_by_did.pop(did, None)

    @callback
    def _async_hand_over(did: str) -> None:
        entities = entities_by_did.pop(did, None)
        for entity in entities if isinstance(entities, list) else [entities]:
            if entity is not None and entity.hass is not None:
                hass.async_create_task(entity.async_remove())

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_WATCH_REMOVED.format(entry.entry_id), _async_forget))
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_WATCH_HANDED_OVER.format(entry.entry_id), _async_hand_over)
    )


class SaveFamilyEntity(CoordinatorEntity[SaveFamilyDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SaveFamilyDataUpdateCoordinator, did: str) -> None:
        super().__init__(coordinator)
        self._did = did

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._did in self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        return watch_device_info(self.snapshot.watch)

    @property
    def snapshot(self):
        return self.coordinator.data[self._did]


class SaveFamilySettingsEntity(CoordinatorEntity[SaveFamilySettingsCoordinator]):
    """Entity backed by the slower settings coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SaveFamilySettingsCoordinator, watch: SaveFamilyWatch, key: str) -> None:
        super().__init__(coordinator)
        self._did = watch.did
        self._attr_unique_id = f"{watch.did}_{key}"
        self._attr_device_info = watch_device_info(watch)

    @property
    def watch_settings(self) -> SaveFamilyWatchSettings | None:
        return (self.coordinator.data or {}).get(self._did)


def watch_device_info(watch: SaveFamilyWatch) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, watch.did)},
        name=watch.name,
        manufacturer=MANUFACTURER,
        model=watch.model or None,
        serial_number=watch.did,
    )
