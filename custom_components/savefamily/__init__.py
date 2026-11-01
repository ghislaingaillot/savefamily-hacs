from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_END,
    ATTR_NUMBERS,
    ATTR_PERIODS,
    ATTR_PHONE_NUMBER,
    ATTR_START,
    CONF_APP_ID,
    CONF_LOGINNAME,
    CONF_PASSWORD,
    CONF_REGION,
    DOMAIN,
    MAX_POSITION_HISTORY,
    SERVICE_CALL_BACK,
    SERVICE_GET_POSITION_HISTORY,
    SERVICE_SET_DND_SCHEDULE,
    SERVICE_SET_SOS_NUMBERS,
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
    client: SaveFamilyApiClient
    coordinator: SaveFamilyDataUpdateCoordinator
    settings_coordinator: SaveFamilySettingsCoordinator


type SaveFamilyConfigEntry = ConfigEntry[SaveFamilyRuntimeData]


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
    await coordinator.async_config_entry_first_refresh()

    # Settings failures must not block location setup.
    settings_coordinator = SaveFamilySettingsCoordinator(hass, client, coordinator)
    await settings_coordinator.async_refresh()

    @callback
    def _async_cleanup_devices() -> None:
        device_reg = dr.async_get(hass)
        known_dids = set(coordinator.data)
        for device in dr.async_entries_for_config_entry(device_reg, entry.entry_id):
            dids = {ident for domain, ident in device.identifiers if domain == DOMAIN}
            if dids and dids.isdisjoint(known_dids):
                device_reg.async_remove_device(device.id)

    _async_cleanup_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_cleanup_devices))

    entry.runtime_data = SaveFamilyRuntimeData(
        client=client,
        coordinator=coordinator,
        settings_coordinator=settings_coordinator,
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.coordinator.async_cancel_delayed_refresh()
        entry.runtime_data.settings_coordinator.async_cancel_delayed_refresh()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SaveFamilyConfigEntry,
    device_entry: "DeviceEntry",
) -> bool:
    coordinator = config_entry.runtime_data.coordinator
    known = {(DOMAIN, did) for did in coordinator.data}
    return not any(ident in known for ident in device_entry.identifiers)


def _async_extract_target(hass: HomeAssistant, call: ServiceCall) -> tuple[set[str], set[str]]:
    """Return (entity_ids, device_ids) referenced by a service call target."""
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
    return selected.referenced | selected.indirectly_referenced, selected.referenced_devices


def _async_resolve_watches(
    hass: HomeAssistant,
    call: ServiceCall,
    predicate: Callable[[SaveFamilyWatchState], bool] = lambda state: True,
) -> list[tuple[str, SaveFamilyRuntimeData]]:
    """Map a service target to (did, runtime data) of loaded watches matching predicate."""
    candidates: dict[str, SaveFamilyRuntimeData] = {
        did: entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        for did, state in (entry.runtime_data.coordinator.data or {}).items()
        if predicate(state)
    }

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    entity_ids, device_ids = _async_extract_target(hass, call)
    device_ids |= {
        entity.device_id
        for entity_id in entity_ids
        if (entity := entity_registry.async_get(entity_id)) is not None and entity.device_id
    }
    watches = sorted(
        {
            did
            for device_id in device_ids
            if (device := device_registry.async_get(device_id)) is not None
            for domain, did in device.identifiers
            if domain == DOMAIN and did in candidates
        }
    )
    return [(did, candidates[did]) for did in watches]


async def _async_handle_set_dnd_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        periods = [DndPeriod.parse(value) for value in call.data[ATTR_PERIODS]]
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    watches = _async_resolve_watches(hass, call, lambda state: supports_dnd_schedule(state.watch.config))
    if not watches:
        raise HomeAssistantError("No SaveFamily watch advertising DC:2 matched the target")

    for did, runtime in watches:
        try:
            await runtime.client.async_set_dnd_schedule(did, periods)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await runtime.settings_coordinator.async_request_refresh()


async def _async_handle_set_sos_numbers(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        numbers = [normalize_phone_number(value) for value in call.data[ATTR_NUMBERS] if value.strip()]
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    watches = _async_resolve_watches(hass, call)
    if not watches:
        raise HomeAssistantError("No SaveFamily watch matched the target")

    for did, runtime in watches:
        watch_settings = (runtime.settings_coordinator.data or {}).get(did)
        if watch_settings is None or watch_settings.settings is None:
            raise HomeAssistantError(f"Settings of watch {did} are not loaded yet; try again later")
        try:
            await runtime.client.async_set_sos_numbers(did, watch_settings.settings, numbers)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await runtime.settings_coordinator.async_request_refresh()


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
        try:
            points = await runtime.client.async_find_position_history(did, start, end)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        response[did] = {"watch": runtime.coordinator.data[did].watch.name, "positions": points}
    return response
