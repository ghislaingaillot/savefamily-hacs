from __future__ import annotations

import logging
from dataclasses import replace

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    EVENT_ALARM,
    POLL_INTERVAL,
    REQUEST_LOCATION_REFRESH_DELAY,
    SETTINGS_POLL_INTERVAL,
)
from .core.async_client import SaveFamilyApiClient
from .core.protocol import (
    COMMAND_REQUEST_LOCATION,
    DEVICE_OFFLINE_STATUS,
    SaveFamilyAuthError,
    SaveFamilyError,
    SaveFamilyResponseError,
    SaveFamilyUpgradeRequiredError,
    SaveFamilyWatchSettings,
    SaveFamilyWatchState,
    extract_settings,
)

_LOGGER = logging.getLogger(__name__)


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
    def __init__(self, hass: HomeAssistant, client: SaveFamilyApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.client = client
        # Highest alarm id seen per watch; alarms present at the first poll are not replayed.
        self._last_alarm_id: dict[str, int] = {}

    async def _async_update_data(self) -> dict[str, SaveFamilyWatchState]:
        try:
            states = await self.client.async_refresh_watch_states(self.data)
            for did, state in states.items():
                states[did] = replace(state, new_alarms=tuple(await self._async_new_alarms(did)))
        except SaveFamilyUpgradeRequiredError as exc:
            raise ConfigEntryError(str(exc)) from exc
        except SaveFamilyAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SaveFamilyError as exc:
            raise UpdateFailed(str(exc)) from exc

        for did, state in states.items():
            for alarm in state.new_alarms:
                self.hass.bus.async_fire(
                    EVENT_ALARM,
                    {"did": did, "watch": state.watch.name, **alarm.as_dict()},
                )
        return states

    async def _async_new_alarms(self, did: str):
        try:
            alarms = await self.client.async_find_alarms(did)
        except (SaveFamilyAuthError, SaveFamilyUpgradeRequiredError):
            raise
        except SaveFamilyError as exc:
            _LOGGER.debug("v2_findAlarmInfo failed for %s: %s", did, exc)
            return []

        newest = max((alarm.id for alarm in alarms), default=0)
        if did not in self._last_alarm_id:
            self._last_alarm_id[did] = newest
            return []
        last_seen = self._last_alarm_id[did]
        self._last_alarm_id[did] = max(last_seen, newest)
        return [alarm for alarm in alarms if alarm.id > last_seen]

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
        results: dict[str, SaveFamilyWatchSettings] = {}
        for did in self._main_coordinator.data or {}:
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
        return results

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
