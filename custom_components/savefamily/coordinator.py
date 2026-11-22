from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DATA_LAST_ALARM_IDS,
    DATA_WATCH_OWNERS,
    DOMAIN,
    EVENT_ALARM,
    POLL_INTERVAL,
    REQUEST_LOCATION_REFRESH_DELAY,
    SETTINGS_POLL_INTERVAL,
    SIGNAL_WATCH_HANDED_OVER,
    STALE_DEVICE_AFTER,
)
from .core.protocol import (
    COMMAND_REQUEST_LOCATION,
    DEVICE_OFFLINE_STATUS,
    SaveFamilyAlarm,
    SaveFamilyAuthError,
    SaveFamilyError,
    SaveFamilyResponseError,
    SaveFamilyUpgradeRequiredError,
    SaveFamilyWatchSettings,
    SaveFamilyWatchState,
    extract_settings,
)

if TYPE_CHECKING:
    from .core.async_client import SaveFamilyApiClient
    from .core.protocol import SaveFamilyClient

_LOGGER = logging.getLogger(__name__)


class _SkippedWatches:
    """Watches the client lists without reading them: another entry handles them."""

    def __init__(self, may_handle: Callable[[str], bool]) -> None:
        self._may_handle = may_handle
        self.skipped: set[str] = set()

    def __contains__(self, did: object) -> bool:
        if not isinstance(did, str) or self._may_handle(did):
            return False
        self.skipped.add(did)
        return True


class _DelayedRefreshMixin:
    """One pending delayed refresh at a time, cancelled on shutdown."""

    hass: HomeAssistant
    _delayed_refresh_unsub: CALLBACK_TYPE | None = None

    @callback
    def async_cancel_delayed_refresh(self) -> None:
        if self._delayed_refresh_unsub is not None:
            self._delayed_refresh_unsub()
            self._delayed_refresh_unsub = None

    @callback
    def async_schedule_delayed_refresh(self, delay: float) -> None:
        if self._delayed_refresh_unsub is not None:
            return
        self._delayed_refresh_unsub = async_call_later(self.hass, delay, self._async_handle_delayed_refresh)

    @callback
    def _async_handle_delayed_refresh(self, _now) -> None:
        self._delayed_refresh_unsub = None
        self.hass.async_create_task(self.async_request_refresh())


class SaveFamilyDataUpdateCoordinator(_DelayedRefreshMixin, DataUpdateCoordinator[dict[str, SaveFamilyWatchState]]):
    def __init__(self, hass: HomeAssistant, client: SaveFamilyClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.client = client
        # When a successful poll last listed each watch this entry handles.
        self.last_shown: dict[str, datetime] = {}
        # Watches the account listed at the last successful poll, handled here or not.
        self.listed: set[str] = set()

    # A watch listed by several accounts (two parents) is handled by one entry at a time:
    # its entities, alarms and settings belong to that entry only.

    @property
    def _owners(self) -> dict[str, str]:
        """Entry id handling each watch, shared by all entries."""
        return self.hass.data.setdefault(DATA_WATCH_OWNERS, {})

    @property
    def _last_alarm_ids(self) -> dict[str, int]:
        """Highest alarm id seen per watch, shared so that it follows a watch to another entry."""
        return self.hass.data.setdefault(DATA_LAST_ALARM_IDS, {})

    async def _async_update_data(self) -> dict[str, SaveFamilyWatchState]:
        entry_id = self.config_entry.entry_id
        try:
            # Listed by the account but handled by another entry: not read.
            skip = _SkippedWatches(self._async_may_handle)
            listed = await self.client.async_refresh_watch_states(self.data, skip=skip)
            states = {
                did: state
                for did, state in listed.items()
                if did not in skip.skipped and self._async_may_handle(did)
            }
            last_alarm_ids: dict[str, int | None] = {}
            for did, state in states.items():
                new_alarms, last_alarm_ids[did] = await self._async_new_alarms(did)
                states[did] = replace(state, new_alarms=tuple(new_alarms))
        except SaveFamilyUpgradeRequiredError as exc:
            raise ConfigEntryError(str(exc)) from exc
        except SaveFamilyAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SaveFamilyError as exc:
            raise UpdateFailed(str(exc)) from exc

        # Claim the watches and store the alarms seen only now that every read succeeded: after an
        # error, the next poll finds these alarms again.
        now = dt_util.utcnow()
        self.listed = set(listed)
        for did in list(states):
            if (owner := self._owners.get(did)) is None:
                self._owners[did] = entry_id
                self._async_adopt_entities(did)
            elif owner != entry_id:
                # Claimed by another entry while this poll was running.
                del states[did]
                continue
            self.last_shown[did] = now
            if (last_alarm_id := last_alarm_ids[did]) is not None:
                self._last_alarm_ids[did] = last_alarm_id
        self._async_take_over_stale_watches(listed.keys() - states.keys(), now)

        for did, state in states.items():
            for alarm in state.new_alarms:
                self.hass.bus.async_fire(
                    EVENT_ALARM,
                    {"did": did, "watch": state.watch.name, **alarm.as_dict()},
                )
        return states

    @callback
    def _async_may_handle(self, did: str) -> bool:
        owner = self._owners.get(did)
        if owner is not None:
            return owner == self.config_entry.entry_id
        # After a restart, leave the watch to the entry that had it while that entry is starting.
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id("device_tracker", DOMAIN, f"{did}_location")
        previous_id = registry.async_get(entity_id).config_entry_id if entity_id else None
        if previous_id in (None, self.config_entry.entry_id):
            return True
        previous = self.hass.config_entries.async_get_entry(previous_id)
        return previous is None or previous.state is not ConfigEntryState.SETUP_IN_PROGRESS

    @callback
    def _async_take_over_stale_watches(self, dids: Iterable[str], now: datetime) -> None:
        """Take over the watches listed here that their entry has not shown for STALE_DEVICE_AFTER.

        That entry failed, needs a new login, or its account no longer lists the watch.
        """
        for did in dids:
            owner = self._owners.get(did)
            if owner is None or owner == self.config_entry.entry_id:
                continue
            entry = self.hass.config_entries.async_get_entry(owner)
            if entry is not None and entry.state is ConfigEntryState.SETUP_IN_PROGRESS:
                # It has just claimed the watch and cannot hand it over before it is loaded.
                continue
            if entry is not None and entry.state is ConfigEntryState.LOADED:
                shown = entry.runtime_data.coordinator.last_shown.get(did)
                if shown is not None and now - shown < STALE_DEVICE_AFTER:
                    continue
            # That entry removes its entities, keeping their registry entries for this one, which
            # claims the watch at its next poll.
            del self._owners[did]
            async_dispatcher_send(self.hass, SIGNAL_WATCH_HANDED_OVER.format(owner), did)

    @callback
    def _async_adopt_entities(self, did: str) -> None:
        """Enable the watch's device and entities, disabled with an entry that had the watch."""
        device_reg = dr.async_get(self.hass)
        if (device := device_reg.async_get_device(identifiers={(DOMAIN, did)})) is None:
            return
        if device.disabled_by is dr.DeviceEntryDisabler.CONFIG_ENTRY:
            device_reg.async_update_device(device.id, disabled_by=None)
        entity_reg = er.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        for entity in er.async_entries_for_device(entity_reg, device.id, include_disabled_entities=True):
            if (
                entity.platform == DOMAIN
                and entity.config_entry_id != entry_id
                and entity.disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY
            ):
                entity_reg.async_update_entity(entity.entity_id, config_entry_id=entry_id, disabled_by=None)

    @callback
    def async_owner(self, did: str) -> str | None:
        """Id of the entry handling the watch, if any."""
        return self._owners.get(did)

    @callback
    def async_listed_by_another_entry(self, did: str) -> bool:
        """Whether another loaded entry's account lists the watch."""
        return any(
            entry.entry_id != self.config_entry.entry_id
            and entry.state is ConfigEntryState.LOADED
            and did in entry.runtime_data.coordinator.listed
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    @callback
    def async_release_watches(self, keep: set[str] | frozenset[str] = frozenset()) -> None:
        """Let other entries take over the watches of this entry, except those in keep."""
        entry_id = self.config_entry.entry_id
        for did in [did for did, owner in self._owners.items() if owner == entry_id and did not in keep]:
            del self._owners[did]

    @callback
    def async_hand_over_watch(self, did: str) -> None:
        """Forget a watch another entry has taken over."""
        self.last_shown.pop(did, None)
        if self.data:
            self.data.pop(did, None)

    async def _async_new_alarms(self, did: str) -> tuple[list[SaveFamilyAlarm], int | None]:
        """New alarms of the watch, and the highest alarm id now seen (None leaves it unchanged)."""
        try:
            alarms = await self.client.async_find_alarms(did)
        except (SaveFamilyAuthError, SaveFamilyUpgradeRequiredError):
            raise
        except SaveFamilyError as exc:
            _LOGGER.debug("v2_findAlarmInfo failed for %s: %s", did, exc)
            return [], None

        newest = max((alarm.id for alarm in alarms), default=0)
        if did not in self._last_alarm_ids:
            # Alarms already there when Home Assistant starts are not replayed.
            return [], newest
        last_seen = self._last_alarm_ids[did]
        return [alarm for alarm in alarms if alarm.id > last_seen], max(last_seen, newest)

    async def async_request_location(self, did: str) -> None:
        if await self.async_send_command(did, COMMAND_REQUEST_LOCATION, with_model=True):
            self.async_schedule_delayed_refresh(REQUEST_LOCATION_REFRESH_DELAY)

    async def async_send_command(self, did: str, command: str, *params: str, with_model: bool = False) -> bool:
        """Send a watch command; return False, with a notification, when the watch is offline."""
        try:
            await self.client.async_send_command(did, command, *params, with_model=with_model)
        except SaveFamilyUpgradeRequiredError as exc:
            raise ConfigEntryError(str(exc)) from exc
        except SaveFamilyAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SaveFamilyResponseError as exc:
            if exc.status == DEVICE_OFFLINE_STATUS:
                self.async_notify_offline(did, exc.message)
                return False
            raise UpdateFailed(str(exc)) from exc
        except SaveFamilyError as exc:
            raise UpdateFailed(str(exc)) from exc

        persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_{did}_offline")
        return True

    @callback
    def async_notify_offline(self, did: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            f"{self.data[did].watch.name} is offline",
            f"{DOMAIN}_{did}_offline",
        )


class SaveFamilySettingsCoordinator(_DelayedRefreshMixin, DataUpdateCoordinator[dict[str, SaveFamilyWatchSettings]]):
    """Poll settings independently so failures do not interrupt location updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SaveFamilyApiClient,
        main_coordinator: SaveFamilyDataUpdateCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_settings",
            update_interval=SETTINGS_POLL_INTERVAL,
        )
        self.client = client
        self._main_coordinator = main_coordinator

    async def _async_update_data(self) -> dict[str, SaveFamilyWatchSettings]:
        previous = self.data or {}
        watches = self._main_coordinator.data or {}
        # Keep the settings of a watch missing from the last poll: it is usually back at the next
        # one. They are dropped once its device is removed or another entry takes it over.
        results = {did: settings for did, settings in previous.items() if did not in watches}
        for did in list(watches):
            old = previous.get(did) or SaveFamilyWatchSettings()
            results[did] = SaveFamilyWatchSettings(
                settings=await self._async_fetch(
                    did, "v2_findSetInfo", self._async_find_settings, old.settings
                ),
                safe_zones=await self._async_fetch(
                    did, "v2_new_findRailInfo", self.client.async_find_safe_zones, old.safe_zones
                ),
                latest_photo=await self._async_fetch(
                    did, "v2_findPictrueDoorInfo", self.client.async_find_latest_photo, old.latest_photo
                ),
            )
        # A watch forgotten while this refresh ran (device removed, taken over) stays forgotten.
        watches, current = self._main_coordinator.data or {}, self.data or {}
        return {did: settings for did, settings in results.items() if did in watches or did in current}

    @callback
    def async_forget_watch(self, did: str) -> None:
        if self.data:
            self.data.pop(did, None)

    async def _async_find_settings(self, did: str):
        return extract_settings(await self.client.async_find_set_info(did))

    async def _async_fetch(self, did: str, name: str, fetch, fallback):
        """Keep the previous value on a transient failure of one request."""
        try:
            return await fetch(did)
        except SaveFamilyUpgradeRequiredError as exc:
            raise ConfigEntryError(str(exc)) from exc
        except SaveFamilyAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SaveFamilyError as exc:
            _LOGGER.debug("%s failed for %s: %s", name, did, exc)
            return fallback
