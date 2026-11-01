from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .core.protocol import supports_dnd_schedule
from .entity import SaveFamilyEntity, SaveFamilySettingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    settings_coordinator = entry.runtime_data.settings_coordinator
    entities_by_did: dict[str, list[SensorEntity]] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                watch = coordinator.data[did].watch
                es = [
                    SaveFamilyBatterySensor(coordinator, did),
                    SaveFamilyLastFixSensor(coordinator, did),
                    SaveFamilyStepsSensor(coordinator, did),
                    SaveFamilySignalSensor(coordinator, did),
                    SaveFamilySosNumbersSensor(settings_coordinator, watch, "sos_numbers"),
                    SaveFamilySafeZonesSensor(settings_coordinator, watch, "safe_zones"),
                ]
                if supports_dnd_schedule(watch.config):
                    es.append(SaveFamilyDndScheduleSensor(settings_coordinator, watch, "dnd_schedule"))
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


class SaveFamilySignalSensor(SaveFamilyEntity, SensorEntity):
    """Raw GSMSingalResponse.signal value; the app shows it as signal bars."""

    _attr_translation_key = "signal"
    _attr_icon = "mdi:signal"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_signal"

    @property
    def native_value(self) -> int | None:
        return self.snapshot.signal


class SaveFamilySosNumbersSensor(SaveFamilySettingsEntity, SensorEntity):
    _attr_translation_key = "sos_numbers"
    _attr_icon = "mdi:phone-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def _numbers(self) -> list[str] | None:
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.settings is None:
            return None
        return [number for number in watch_settings.settings.sos_numbers if number]

    @property
    def native_value(self) -> int | None:
        numbers = self._numbers()
        return None if numbers is None else len(numbers)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        numbers = self._numbers()
        if numbers is None:
            return {}
        attrs: dict[str, Any] = {"numbers": numbers}
        if center := self.watch_settings.settings.center_number:
            attrs["center_number"] = center
        return attrs


class SaveFamilySafeZonesSensor(SaveFamilySettingsEntity, SensorEntity):
    _attr_translation_key = "safe_zones"
    _attr_icon = "mdi:map-marker-radius"

    @property
    def native_value(self) -> int | None:
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.safe_zones is None:
            return None
        return len(watch_settings.safe_zones)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.safe_zones is None:
            return {}
        return {"zones": [zone.as_dict() for zone in watch_settings.safe_zones]}


class SaveFamilyDndScheduleSensor(SaveFamilySettingsEntity, SensorEntity):
    """Configured schedule, not the watch's current DND state."""

    _attr_translation_key = "dnd_schedule"
    _attr_icon = "mdi:bell-sleep"

    def _periods(self):
        watch_settings = self.watch_settings
        if watch_settings is None or watch_settings.settings is None:
            return None
        return watch_settings.settings.dnd_periods

    @property
    def native_value(self) -> str | None:
        periods = self._periods()
        if periods is None:
            return None
        return "; ".join(str(period) for period in periods) or "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        periods = self._periods()
        if periods is None:
            return {}
        return {
            "periods": [
                {"start": period.start, "end": period.end, "weekdays": period.weekday_names}
                for period in periods
            ]
        }
