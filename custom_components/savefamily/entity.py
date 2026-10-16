from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SaveFamilyDataUpdateCoordinator
from .core.protocol import SaveFamilyWatch


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


def watch_device_info(watch: SaveFamilyWatch) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, watch.did)},
        name=watch.name,
        manufacturer=MANUFACTURER,
        model=watch.model or None,
        serial_number=watch.did,
    )
