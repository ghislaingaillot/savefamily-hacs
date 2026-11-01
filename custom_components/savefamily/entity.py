from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilySettingsCoordinator
from .core.protocol import SaveFamilyWatch, SaveFamilyWatchSettings


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
