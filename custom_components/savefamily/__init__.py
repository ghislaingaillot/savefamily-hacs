from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_END,
    ATTR_NUMBERS,
    ATTR_PERIODS,
    ATTR_PHONE_NUMBER,
    ATTR_START,
    BACKEND_CLASSIC,
    BACKEND_CONNECT,
    CONF_APP_ID,
    CONF_BACKEND,
    CONF_LOGINNAME,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    DOMAIN,
    MAX_POSITION_HISTORY,
    SERVICE_CALL_BACK,
    SERVICE_GET_POSITION_HISTORY,
    SERVICE_SET_DND_SCHEDULE,
    SERVICE_SET_SOS_NUMBERS,
    SIGNAL_WATCH_HANDED_OVER,
    SIGNAL_WATCH_REMOVED,
    STALE_DEVICE_AFTER,
)
from .core.protocol import (
    COMMAND_CALL_BACK,
    DEFAULT_APP_ID,
    MAX_DND_PERIODS,
    MAX_SOS_NUMBERS,
    DndPeriod,
    SaveFamilyError,
    normalize_phone_number,
    supports_dnd_schedule,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
    from homeassistant.helpers.device_registry import DeviceEntry

    from .connect.client import SaveFamilyConnectClient
    from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilySettingsCoordinator
    from .core.async_client import SaveFamilyApiClient
    from .core.protocol import SaveFamilyWatchState

PLATFORMS = (
    "device_tracker",
    "sensor",
    "button",
    "binary_sensor",
    "select",
    "event",
    "image",
)
# Connect has no Classic settings, commands or photo wall (yet).
CONNECT_PLATFORMS = (
    "device_tracker",
    "sensor",
    "binary_sensor",
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_DND_SCHEDULE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_PERIODS): vol.All(list, [cv.string], vol.Length(max=MAX_DND_PERIODS)),
    }
)
SET_SOS_NUMBERS_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_NUMBERS): vol.All(list, [cv.string], vol.Length(max=MAX_SOS_NUMBERS)),
    }
)
CALL_BACK_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_PHONE_NUMBER): cv.string,
    }
)
GET_POSITION_HISTORY_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
    }
)


@dataclass
class SaveFamilyRuntimeData:
    client: SaveFamilyApiClient | SaveFamilyConnectClient
    coordinator: SaveFamilyDataUpdateCoordinator
    # None for Connect entries, which have no Classic settings.
    settings_coordinator: SaveFamilySettingsCoordinator | None

    @property
    def classic(self) -> tuple[SaveFamilyApiClient, SaveFamilySettingsCoordinator]:
        """Client and settings of a Classic entry, for the Classic-only actions."""
        from .core.async_client import SaveFamilyApiClient

        if not isinstance(self.client, SaveFamilyApiClient) or self.settings_coordinator is None:
            raise HomeAssistantError("This action is not supported on SaveFamily Connect yet")
        return self.client, self.settings_coordinator


type SaveFamilyConfigEntry = ConfigEntry[SaveFamilyRuntimeData]


def _platforms(entry: SaveFamilyConfigEntry) -> tuple[str, ...]:
    return CONNECT_PLATFORMS if entry.data.get(CONF_BACKEND) == BACKEND_CONNECT else PLATFORMS


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DND_SCHEDULE,
        partial(_async_handle_set_dnd_schedule, hass),
        schema=SET_DND_SCHEDULE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SOS_NUMBERS,
        partial(_async_handle_set_sos_numbers, hass),
        schema=SET_SOS_NUMBERS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALL_BACK,
        partial(_async_handle_call_back, hass),
        schema=CALL_BACK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_POSITION_HISTORY,
        partial(_async_handle_get_position_history, hass),
        schema=GET_POSITION_HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    if entry.data.get(CONF_BACKEND, BACKEND_CLASSIC) == BACKEND_CONNECT:
        return await _async_setup_connect_entry(hass, entry)

    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilySettingsCoordinator
    from .core.async_client import SaveFamilyApiClient

    session = async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    client = SaveFamilyApiClient(
        session,
        region=entry.data[CONF_REGION],
        loginname=entry.data[CONF_LOGINNAME],
        password=entry.data[CONF_PASSWORD],
        app_id=entry.data.get(CONF_APP_ID, DEFAULT_APP_ID),
    )
    coordinator = SaveFamilyDataUpdateCoordinator(hass, client)
    # Another entry (second parent account) may then take over the watches of this one.
    entry.async_on_unload(coordinator.async_release_watches)
    await coordinator.async_config_entry_first_refresh()

    # Settings failures must not block location setup.
    settings_coordinator = SaveFamilySettingsCoordinator(hass, client, coordinator)
    await settings_coordinator.async_refresh()

    _async_track_devices(hass, entry, coordinator, settings_coordinator)
    entry.runtime_data = SaveFamilyRuntimeData(
        client=client,
        coordinator=coordinator,
        settings_coordinator=settings_coordinator,
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_connect_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .connect.client import SaveFamilyConnectClient
    from .coordinator import SaveFamilyDataUpdateCoordinator

    @callback
    def _async_store_refresh_token(token: str) -> None:
        # The server rotates the refresh token on every use; losing the new one ends the session.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: token})

    # Tokens are sent explicitly; keep the account's cookies out of any shared jar.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    client = SaveFamilyConnectClient(
        session,
        email=entry.data[CONF_LOGINNAME],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        on_refresh_token=_async_store_refresh_token,
    )

    async def _async_wait_for_token_refresh(_event: Event) -> None:
        await client.async_wait_for_token_refresh()

    # Shutdown cancels the polls: let a token rotation already sent to the server be stored.
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_wait_for_token_refresh))
    coordinator = SaveFamilyDataUpdateCoordinator(hass, client)
    # Another entry (second parent account) may then take over the watches of this one.
    entry.async_on_unload(coordinator.async_release_watches)
    await coordinator.async_config_entry_first_refresh()

    _async_track_devices(hass, entry, coordinator, None)
    entry.runtime_data = SaveFamilyRuntimeData(
        client=client,
        coordinator=coordinator,
        settings_coordinator=None,
    )
    # No reload-on-update listener: every token rotation updates the entry data.
    await hass.config_entries.async_forward_entry_setups(entry, CONNECT_PLATFORMS)
    return True


@callback
def _async_track_devices(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    coordinator: SaveFamilyDataUpdateCoordinator,
    settings_coordinator: SaveFamilySettingsCoordinator | None,
) -> None:
    @callback
    def _async_handed_over(did: str) -> None:
        coordinator.async_hand_over_watch(did)
        if settings_coordinator is not None:
            settings_coordinator.async_forget_watch(did)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_WATCH_HANDED_OVER.format(entry.entry_id), _async_handed_over)
    )
    if settings_coordinator is not None:
        entry.async_on_unload(
            async_dispatcher_connect(
                hass, SIGNAL_WATCH_REMOVED.format(entry.entry_id), settings_coordinator.async_forget_watch
            )
        )

    # Since when each device's watch has been missing from every successful poll.
    missing_since: dict[str, datetime] = {}

    @callback
    def _async_cleanup_devices() -> None:
        if not coordinator.last_update_success:
            # A failed poll keeps the previous data and says nothing about the account: a missing
            # watch must then be missing from successful polls for STALE_DEVICE_AFTER again.
            missing_since.clear()
            return
        device_reg = dr.async_get(hass)
        devices = dr.async_entries_for_config_entry(device_reg, entry.entry_id)
        # A device the user removed starts over if it comes back (Home Assistant restores its id).
        for device_id in missing_since.keys() - {device.id for device in devices}:
            del missing_since[device_id]
        known_dids = set(coordinator.data)
        # Watches this entry still shows: listed by the last poll, or with a device kept for now.
        shown_dids = set(known_dids)
        now = dt_util.utcnow()
        for device in devices:
            dids = {ident for domain, ident in device.identifiers if domain == DOMAIN}
            if not dids or not dids.isdisjoint(known_dids):
                missing_since.pop(device.id, None)
                continue
            # The server sometimes leaves a watch out of a poll or two. And when another account lists
            # the watch, keep the device until that entry has taken over its entities.
            if now - missing_since.setdefault(device.id, now) < STALE_DEVICE_AFTER or any(
                coordinator.async_owner(did) in (None, entry.entry_id)
                and coordinator.async_listed_by_another_entry(did)
                for did in dids
            ):
                shown_dids |= dids
                continue
            del missing_since[device.id]
            # Removes only this entry's link: another entry (second parent account) may share the watch.
            device_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
            _async_forget_watches(hass, entry, dids)
        coordinator.async_release_watches(shown_dids)

    _async_cleanup_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_cleanup_devices))


async def async_unload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _platforms(entry))
    if unload_ok:
        entry.runtime_data.coordinator.async_cancel_delayed_refresh()
        if entry.runtime_data.settings_coordinator is not None:
            entry.runtime_data.settings_coordinator.async_cancel_delayed_refresh()
        if entry.data.get(CONF_BACKEND) == BACKEND_CONNECT:
            # A reload must not start before a token rotation in progress is stored.
            await entry.runtime_data.client.async_wait_for_token_refresh()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SaveFamilyConfigEntry,
    device_entry: "DeviceEntry",
) -> bool:
    if config_entry.state is not ConfigEntryState.LOADED:
        # No poll to check against and no platform to tell (setup failed or entry disabled).
        return True
    coordinator = config_entry.runtime_data.coordinator
    known = {(DOMAIN, did) for did in coordinator.data}
    if any(ident in known for ident in device_entry.identifiers):
        return False
    _async_forget_watches(hass, config_entry, {ident for domain, ident in device_entry.identifiers if domain == DOMAIN})
    return True


@callback
def _async_forget_watches(hass: HomeAssistant, entry: SaveFamilyConfigEntry, dids: set[str]) -> None:
    """Tell the platforms to create the entities of these watches again if they come back."""
    for did in dids:
        async_dispatcher_send(hass, SIGNAL_WATCH_REMOVED.format(entry.entry_id), did)


def _async_extract_target(hass: HomeAssistant, call: ServiceCall) -> tuple[set[str], set[str], set[str]]:
    """Return (entity_ids, device_ids, explicit_ids) referenced by a service call target.

    explicit_ids are the entities and devices named in the target, not found through an area,
    floor or label.
    """
    try:
        from homeassistant.helpers.target import (
            TargetSelection,
            async_extract_referenced_entity_ids,
        )
    except ImportError:  # Home Assistant before the target helper module
        from homeassistant.helpers.service import async_extract_referenced_entity_ids

        selected = async_extract_referenced_entity_ids(hass, call)
    else:
        selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    explicit_ids = selected.referenced | set(cv.ensure_list(call.data.get(ATTR_DEVICE_ID)))
    return selected.referenced | selected.indirectly_referenced, selected.referenced_devices, explicit_ids


def _async_resolve_watches(
    hass: HomeAssistant,
    call: ServiceCall,
    predicate: Callable[[SaveFamilyWatchState], bool] = lambda state: True,
) -> list[tuple[str, SaveFamilyRuntimeData]]:
    """Map a service target to (did, runtime data) of loaded Classic watches matching predicate.

    The actions are Classic-only for now. A Connect watch found through an area, floor or label is
    skipped; one named in the target, or a target with no Classic watch, raises.
    """
    candidates: dict[str, tuple[SaveFamilyRuntimeData, SaveFamilyWatchState]] = {
        did: (entry.runtime_data, state)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        for did, state in (entry.runtime_data.coordinator.data or {}).items()
    }

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    entity_ids, device_ids, explicit_ids = _async_extract_target(hass, call)

    def _watches(ids: set[str]) -> set[str]:
        """Loaded watches of these entity and device ids."""
        ids = ids | {
            entity.device_id
            for entity_id in ids
            if (entity := entity_registry.async_get(entity_id)) is not None and entity.device_id
        }
        return {
            did
            for device_id in ids
            if (device := device_registry.async_get(device_id)) is not None
            for domain, did in device.identifiers
            if domain == DOMAIN and did in candidates
        }

    watches = sorted(_watches(entity_ids | device_ids))
    connect = {did for did in watches if candidates[did][0].settings_coordinator is None}
    if connect:
        if connect == set(watches) or not connect.isdisjoint(_watches(explicit_ids)):
            raise HomeAssistantError("This action is not supported on SaveFamily Connect yet")
        watches = [did for did in watches if did not in connect]
    matches = []
    for did in watches:
        runtime, state = candidates[did]
        if predicate(state):
            matches.append((did, runtime))
    return matches


async def _async_handle_set_dnd_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        periods = [DndPeriod.parse(value) for value in call.data[ATTR_PERIODS]]
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    watches = _async_resolve_watches(hass, call, lambda state: supports_dnd_schedule(state.watch.config))
    if not watches:
        raise HomeAssistantError("No SaveFamily watch advertising DC:2 matched the target")

    for did, runtime in watches:
        client, settings_coordinator = runtime.classic
        try:
            await client.async_set_dnd_schedule(did, periods)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await settings_coordinator.async_request_refresh()


async def _async_handle_set_sos_numbers(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        numbers = [normalize_phone_number(value) for value in call.data[ATTR_NUMBERS] if value.strip()]
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    watches = _async_resolve_watches(hass, call)
    if not watches:
        raise HomeAssistantError("No SaveFamily watch matched the target")

    for did, runtime in watches:
        client, settings_coordinator = runtime.classic
        watch_settings = (settings_coordinator.data or {}).get(did)
        if watch_settings is None or watch_settings.settings is None:
            raise HomeAssistantError(f"Settings of watch {did} are not loaded yet; try again later")
        try:
            await client.async_set_sos_numbers(did, watch_settings.settings, numbers)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await settings_coordinator.async_request_refresh()


async def _async_handle_call_back(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        number = normalize_phone_number(call.data[ATTR_PHONE_NUMBER])
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    watches = _async_resolve_watches(hass, call)
    if not watches:
        raise HomeAssistantError("No SaveFamily watch matched the target")

    for did, runtime in watches:
        await runtime.coordinator.async_send_command(did, COMMAND_CALL_BACK, number)


def _as_local(value: datetime) -> datetime:
    """Naive times are Home Assistant local time, like the app's phone-local query."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.now().tzinfo)
    return dt_util.as_local(value)


async def _async_handle_get_position_history(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    start = _as_local(call.data[ATTR_START])
    end = _as_local(call.data.get(ATTR_END) or dt_util.now())
    if not start < end <= start + MAX_POSITION_HISTORY:
        raise HomeAssistantError("start must be before end, and the range at most 24 hours")

    watches = _async_resolve_watches(hass, call)
    if not watches:
        raise HomeAssistantError("No SaveFamily watch matched the target")

    response: dict[str, Any] = {}
    for did, runtime in watches:
        client, _ = runtime.classic
        try:
            points = await client.async_find_position_history(did, start, end)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        response[did] = {"watch": runtime.coordinator.data[did].watch.name, "positions": points}
    return response
