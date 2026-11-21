"""Devices of watches that leave the account, or miss a few polls, then come back."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.savefamily.const import (
    DOMAIN,
    POLL_INTERVAL,
    SIGNAL_WATCH_HANDED_OVER,
    STALE_DEVICE_AFTER,
)

from .conftest import FakeApi

# Scheduled polls, the first one included, before a missing watch's device is removed.
POLLS_BEFORE_REMOVAL = STALE_DEVICE_AFTER // POLL_INTERVAL + 1


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    times: int = 1,
    freezer: FrozenDateTimeFactory | None = None,
) -> None:
    """Refresh now, or every POLL_INTERVAL when a freezer is given."""
    for _ in range(times):
        if freezer is not None:
            freezer.tick(POLL_INTERVAL)
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


async def _remove_device(hass: HomeAssistant, hass_ws_client: WebSocketGenerator, entry: MockConfigEntry, did: str) -> bool:
    """Remove the watch's device as the user does from the device page."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": _device(hass, did).id,
        }
    )
    response = await client.receive_json()
    await hass.async_block_till_done()
    return response["success"]


async def test_watch_missing_for_a_while_keeps_its_entities(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")
    assert before
    assert _battery_state(hass, "222") == "80"

    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL - 1, freezer)

    assert _device(hass, "222") is not None
    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == STATE_UNAVAILABLE

    api.dids = ["111", "222"]
    await _poll(hass, config_entry, freezer=freezer)

    assert _battery_state(hass, "222") == "80"


async def test_extra_refreshes_do_not_shorten_the_delay(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Refreshes after commands (refresh location...) come seconds apart."""
    await _setup(hass, config_entry)

    api.dids = ["111"]
    await _poll(hass, config_entry, 10)

    assert _device(hass, "222") is not None


async def test_watch_gone_is_removed_then_recreated_when_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")

    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL, freezer)

    assert _device(hass, "222") is None
    assert _unique_ids(hass, config_entry, "222") == set()
    assert _battery_state(hass, "222") is None
    assert _device(hass, "111") is not None

    api.dids = ["111", "222"]
    await _poll(hass, config_entry, freezer=freezer)

    assert _device(hass, "222") is not None
    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == "80"


async def test_failed_polls_do_not_count_as_missing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    # Missing once, then an outage longer than the delay, then missing once more.
    api.dids = ["111"]
    await _poll(hass, config_entry, freezer=freezer)
    api.fail = True
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL, freezer)
    api.fail = False
    await _poll(hass, config_entry, freezer=freezer)

    assert _device(hass, "222") is not None


async def test_watch_shared_with_another_entry_keeps_its_device(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    # A second parent account that sees the same watch (disabled, so it is not set up here).
    other_entry = MockConfigEntry(
        domain=DOMAIN, unique_id="europe:other@example.com", disabled_by=ConfigEntryDisabler.USER
    )
    other_entry.add_to_hass(hass)
    await _setup(hass, config_entry)
    dr.async_get(hass).async_get_or_create(config_entry_id=other_entry.entry_id, identifiers={(DOMAIN, "222")})

    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL, freezer)

    device = _device(hass, "222")
    assert device is not None
    assert device.config_entries == {other_entry.entry_id}
    assert _unique_ids(hass, config_entry, "222") == set()


async def test_device_removed_by_the_user_comes_back(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    freezer: FrozenDateTimeFactory,
    api: FakeApi,
    config_entry: MockConfigEntry,
) -> None:
    assert await async_setup_component(hass, "config", {})
    await _setup(hass, config_entry)
    before = _unique_ids(hass, config_entry, "222")

    # Refused while the account still lists the watch.
    assert not await _remove_device(hass, hass_ws_client, config_entry, "222")

    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL - 1, freezer)
    assert await _remove_device(hass, hass_ws_client, config_entry, "222")
    assert _device(hass, "222") is None

    api.dids = ["111", "222"]
    await _poll(hass, config_entry, freezer=freezer)

    assert _unique_ids(hass, config_entry, "222") == before
    assert _battery_state(hass, "222") == "80"

    # The time missing before the removal does not count any more.
    api.dids = ["111"]
    await _poll(hass, config_entry, freezer=freezer)

    assert _device(hass, "222") is not None


async def test_device_of_an_entry_not_loaded_can_be_removed(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "config", {})
    api.fail = True
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    dr.async_get(hass).async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "222")})

    assert await _remove_device(hass, hass_ws_client, config_entry, "222")
    assert _device(hass, "222") is None


async def test_settings_of_a_watch_missing_from_a_poll_are_kept(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    settings_coordinator = config_entry.runtime_data.settings_coordinator
    assert "222" in settings_coordinator.data

    api.dids = ["111"]
    await _poll(hass, config_entry)
    await settings_coordinator.async_refresh()

    assert "222" in settings_coordinator.data


async def test_settings_of_a_removed_watch_are_dropped(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    settings_coordinator = config_entry.runtime_data.settings_coordinator

    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL, freezer)
    await settings_coordinator.async_refresh()

    assert set(settings_coordinator.data) == {"111"}


async def test_settings_of_a_watch_handed_over_during_a_refresh_are_dropped(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    settings_coordinator = config_entry.runtime_data.settings_coordinator

    @callback
    def _hand_over(did: str) -> None:
        if did == "222":
            async_dispatcher_send(hass, SIGNAL_WATCH_HANDED_OVER.format(config_entry.entry_id), "222")

    api.on_settings_read = _hand_over
    await settings_coordinator.async_refresh()

    assert set(settings_coordinator.data) == {"111"}
