from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SaveFamilyConfigEntry
from .coordinator import SaveFamilyDndSettingsCoordinator
from .core.protocol import SaveFamilyWatch, supports_dnd_schedule
from .entity import SaveFamilyEntity, watch_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    dnd_coordinator = entry.runtime_data.dnd_coordinator
    entities_by_did: dict[str, list[SensorEntity]] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                es = [
                    SaveFamilyBatterySensor(coordinator, did),
                    SaveFamilyLastFixSensor(coordinator, did),
                    SaveFamilyStepsSensor(coordinator, did),
                ]
                watch = coordinator.data[did].watch
                if supports_dnd_schedule(watch.config):
                    es.append(SaveFamilyDndScheduleSensor(dnd_coordinator, watch))
                entities_by_did[did] = es
                new_entities.extend(es)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


class SaveFamilyBatterySensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_battery"

    @property
    def native_value(self) -> int | None:
        return self.snapshot.battery


class SaveFamilyLastFixSensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "last_fix"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_last_fix"

    @property
    def native_value(self):
        return self.snapshot.last_fix


class SaveFamilyStepsSensor(SaveFamilyEntity, SensorEntity):
    _attr_translation_key = "steps"
    _attr_icon = "mdi:walk"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "steps"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_steps"

    @property
    def native_value(self) -> int | None:
        return self.snapshot.step_count


class SaveFamilyDndScheduleSensor(CoordinatorEntity[SaveFamilyDndSettingsCoordinator], SensorEntity):
    """Configured schedule, not the watch's current DND state."""

    _attr_has_entity_name = True
    _attr_translation_key = "dnd_schedule"
    _attr_icon = "mdi:bell-sleep"

    def __init__(self, coordinator: SaveFamilyDndSettingsCoordinator, watch: SaveFamilyWatch) -> None:
        super().__init__(coordinator)
        self._did = watch.did
        self._attr_unique_id = f"{watch.did}_dnd_schedule"
        self._attr_device_info = watch_device_info(watch)

    @property
    def native_value(self) -> str | None:
        periods = (self.coordinator.data or {}).get(self._did)
        if periods is None:
            return None
        return "; ".join(str(period) for period in periods) or "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        periods = (self.coordinator.data or {}).get(self._did)
        if periods is None:
            return {}
        return {
            "periods": [
                {"start": period.start, "end": period.end, "weekdays": period.weekday_names}
                for period in periods
            ]
        }
