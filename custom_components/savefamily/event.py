from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SaveFamilyConfigEntry
from .core.protocol import ALARM_TYPE_OTHER, ALARM_TYPES
from .entity import SaveFamilyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities_by_did: dict[str, SaveFamilyAlarmEvent] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did in coordinator.data:
            if did not in entities_by_did:
                entity = SaveFamilyAlarmEvent(coordinator, did)
                entities_by_did[did] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


class SaveFamilyAlarmEvent(SaveFamilyEntity, EventEntity):
    """Alarms reported by the watch (SOS, low battery, geofence, removal, fall...)."""

    _attr_translation_key = "alarm"
    _attr_icon = "mdi:alarm-light"
    _attr_event_types = [*ALARM_TYPES.values(), ALARM_TYPE_OTHER]

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_alarm"
        # Listeners also run on failed refreshes with the previous data; never replay an alarm.
        self._last_alarm_id = 0

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._did in self.coordinator.data:
            for alarm in self.snapshot.new_alarms:
                if alarm.id <= self._last_alarm_id:
                    continue
                self._last_alarm_id = alarm.id
                attributes = alarm.as_dict()
                attributes.pop("type")
                self._trigger_event(alarm.type, attributes)
                # One state write per alarm, so several alarms in one poll all reach automations.
                self.async_write_ha_state()
        super()._handle_coordinator_update()
