"""Devices of watches that leave the account, or miss a few polls, then come back."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.savefamily.const import DOMAIN, STALE_DEVICE_POLLS

from .conftest import FakeApi


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry, times: int = 1) -> None:
    for _ in range(times):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()


def _device(hass: HomeAssistant, did: str) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, did)})


def _unique_ids(hass: HomeAssistant, entry: MockConfigEntry, did: str) -> set[str]:
    return {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if entity.unique_id.startswith(f"{did}_")
    }


def _battery_state(hass: HomeAssistant, did: str) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{did}_battery")
    state = hass.states.get(entity_id) if entity_id else None
    return state.state if state else None


async def test_watch_missing_from_a_few_polls_keeps_its_entities(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")
    assert before
    assert _battery_state(hass, "222") == "80"

    api.dids = ["111"]
    await _poll(hass, config_entry, STALE_DEVICE_POLLS - 1)

    assert _device(hass, "222") is not None
    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == STATE_UNAVAILABLE

    api.dids = ["111", "222"]
    await _poll(hass, config_entry)

    assert _battery_state(hass, "222") == "80"


async def test_watch_gone_is_removed_then_recreated_when_back(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")

    api.dids = ["111"]
    await _poll(hass, config_entry, STALE_DEVICE_POLLS)

    assert _device(hass, "222") is None
    assert _unique_ids(hass, config_entry, "222") == set()
    assert _battery_state(hass, "222") is None
    assert _device(hass, "111") is not None

    api.dids = ["111", "222"]
    await _poll(hass, config_entry)

    assert _device(hass, "222") is not None
    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == "80"


async def test_failed_polls_do_not_count_as_missing(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    api.dids = ["111"]
    await _poll(hass, config_entry, STALE_DEVICE_POLLS - 1)
    api.fail = True
    await _poll(hass, config_entry, STALE_DEVICE_POLLS)

    assert _device(hass, "222") is not None

    api.fail = False
    await _poll(hass, config_entry)

    assert _device(hass, "222") is None


async def test_watch_shared_with_another_entry_keeps_its_device(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    # A second parent account that sees the same watch (disabled, so it is not set up here).
    other_entry = MockConfigEntry(
        domain=DOMAIN, unique_id="europe:other@example.com", disabled_by=ConfigEntryDisabler.USER
    )
    other_entry.add_to_hass(hass)
    await _setup(hass, config_entry)
    dr.async_get(hass).async_get_or_create(config_entry_id=other_entry.entry_id, identifiers={(DOMAIN, "222")})

    api.dids = ["111"]
    await _poll(hass, config_entry, STALE_DEVICE_POLLS)

    device = _device(hass, "222")
    assert device is not None
    assert device.config_entries == {other_entry.entry_id}
    assert _unique_ids(hass, config_entry, "222") == set()


async def test_device_removed_by_the_user_comes_back(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "config", {})
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")
    client = await hass_ws_client(hass)

    async def _remove(did: str) -> dict:
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": config_entry.entry_id,
                "device_id": _device(hass, did).id,
            }
        )
        return await client.receive_json()

    # Refused while the account still lists the watch.
    assert not (await _remove("222"))["success"]

    api.dids = ["111"]
    await _poll(hass, config_entry)
    assert (await _remove("222"))["success"]
    await hass.async_block_till_done()
    assert _device(hass, "222") is None

    api.dids = ["111", "222"]
    await _poll(hass, config_entry)

    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == "80"
