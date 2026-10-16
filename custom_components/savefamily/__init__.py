from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_PERIODS,
    CONF_APP_ID,
    CONF_LOGINNAME,
    CONF_PASSWORD,
    CONF_REGION,
    DOMAIN,
    SERVICE_SET_DND_SCHEDULE,
)
from .core.protocol import (
    DEFAULT_APP_ID,
    MAX_DND_PERIODS,
    DndPeriod,
    SaveFamilyError,
    supports_dnd_schedule,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.device_registry import DeviceEntry

    from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilyDndSettingsCoordinator
    from .core.async_client import SaveFamilyApiClient

PLATFORMS = (
    "device_tracker",
    "sensor",
    "button",
    "binary_sensor",
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_DND_SCHEDULE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_PERIODS): vol.All(list, [cv.string], vol.Length(max=MAX_DND_PERIODS)),
    }
)


@dataclass
class SaveFamilyRuntimeData:
    client: SaveFamilyApiClient
    coordinator: SaveFamilyDataUpdateCoordinator
    dnd_coordinator: SaveFamilyDndSettingsCoordinator


type SaveFamilyConfigEntry = ConfigEntry[SaveFamilyRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DND_SCHEDULE,
        partial(_async_handle_set_dnd_schedule, hass),
        schema=SET_DND_SCHEDULE_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .coordinator import SaveFamilyDataUpdateCoordinator, SaveFamilyDndSettingsCoordinator
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
    dnd_coordinator = SaveFamilyDndSettingsCoordinator(hass, client, coordinator)
    await dnd_coordinator.async_refresh()

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
        dnd_coordinator=dnd_coordinator,
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.coordinator.async_shutdown()
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


async def _async_handle_set_dnd_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        periods = [DndPeriod.parse(value) for value in call.data[ATTR_PERIODS]]
    except ValueError as exc:
        raise HomeAssistantError(str(exc)) from exc

    supported: dict[str, SaveFamilyRuntimeData] = {
        did: entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        for did, state in (entry.runtime_data.coordinator.data or {}).items()
        if supports_dnd_schedule(state.watch.config)
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
            if domain == DOMAIN and did in supported
        }
    )
    if not watches:
        raise HomeAssistantError("No SaveFamily watch advertising DC:2 matched the target")

    for did in watches:
        runtime = supported[did]
        try:
            await runtime.client.async_set_dnd_schedule(did, periods)
        except SaveFamilyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await runtime.dnd_coordinator.async_request_refresh()
