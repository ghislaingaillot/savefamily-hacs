from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import CONF_APP_ID, CONF_LOGINNAME, CONF_PASSWORD, CONF_REGION, DOMAIN
from .core.protocol import DEFAULT_APP_ID

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .coordinator import SaveFamilyDataUpdateCoordinator

PLATFORMS = (
    "device_tracker",
    "sensor",
    "button",
    "binary_sensor",
)


@dataclass
class SaveFamilyRuntimeData:
    coordinator: SaveFamilyDataUpdateCoordinator
    session: aiohttp.ClientSession


type SaveFamilyConfigEntry = ConfigEntry[SaveFamilyRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .coordinator import SaveFamilyDataUpdateCoordinator
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

    entry.runtime_data = SaveFamilyRuntimeData(coordinator=coordinator, session=session)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SaveFamilyConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.coordinator.async_shutdown()
        await entry.runtime_data.session.close()
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
