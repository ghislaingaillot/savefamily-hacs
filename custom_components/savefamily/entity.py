from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SIGNAL_WATCH_REMOVED
from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilySettingsCoordinator
from .core.protocol import SaveFamilyWatch, SaveFamilyWatchSettings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


@callback
def async_forget_removed_watches(hass: HomeAssistant, entry: ConfigEntry, entities_by_did: dict[str, Any]) -> None:
    """Let a platform add a watch's entities again if the watch comes back after its device was removed."""

    @callback
    def _async_forget(did: str) -> None:
        entities_by_did.pop(did, None)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_WATCH_REMOVED.format(entry.entry_id), _async_forget))


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
