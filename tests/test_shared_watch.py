"""A watch listed by two accounts (two parents) is handled by one entry at a time."""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.savefamily.const import DOMAIN, EVENT_ALARM, POLL_INTERVAL

from .conftest import FakeApi, make_entry
from .test_init import POLLS_BEFORE_REMOVAL, _battery_state, _device, _poll, _setup

DUPLICATE_ID_ERROR = "does not generate unique IDs"


async def _setup_two_accounts(hass: HomeAssistant, api: FakeApi) -> tuple[MockConfigEntry, MockConfigEntry]:
    """Both parents' accounts list watch 222."""
    api.accounts = {"mum@example.com": ["111", "222"], "dad@example.com": ["222", "333"]}
    mum = make_entry("mum@example.com")
    dad = make_entry("dad@example.com")
    await _setup(hass, mum)
    await _setup(hass, dad)
    return mum, dad


def _entry_of(hass: HomeAssistant, unique_id: str) -> str | None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    return registry.async_get(entity_id).config_entry_id if entity_id else None


async def test_shared_watch_has_one_set_of_entities(
    hass: HomeAssistant, api: FakeApi, caplog: pytest.LogCaptureFixture
) -> None:
    mum, dad = await _setup_two_accounts(hass, api)

    assert set(mum.runtime_data.coordinator.data) == {"111", "222"}
    assert set(dad.runtime_data.coordinator.data) == {"333"}
    assert _entry_of(hass, "222_battery") == mum.entry_id
    assert _entry_of(hass, "333_battery") == dad.entry_id
    assert DUPLICATE_ID_ERROR not in caplog.text


async def test_shared_watch_alarm_is_fired_once(hass: HomeAssistant, api: FakeApi) -> None:
    events = async_capture_events(hass, EVENT_ALARM)
    mum, dad = await _setup_two_accounts(hass, api)

    api.add_alarm("222", "sos")
    await _poll(hass, mum)
    await _poll(hass, dad)

    assert [(event.data["did"], event.data["type"]) for event in events] == [("222", "sos")]


async def test_shared_watch_moves_to_the_other_entry_when_its_entry_unloads(
    hass: HomeAssistant, api: FakeApi, caplog: pytest.LogCaptureFixture
) -> None:
    mum, dad = await _setup_two_accounts(hass, api)

    assert await hass.config_entries.async_unload(mum.entry_id)
    await hass.async_block_till_done()
    await _poll(hass, dad)

    assert set(dad.runtime_data.coordinator.data) == {"222", "333"}
    assert _entry_of(hass, "222_battery") == dad.entry_id
    assert _battery_state(hass, "222") == "80"

    # Back again, mum's entry leaves the watch to dad's.
    assert await hass.config_entries.async_setup(mum.entry_id)
    await hass.async_block_till_done()

    assert set(mum.runtime_data.coordinator.data) == {"111"}
    assert _entry_of(hass, "222_battery") == dad.entry_id
    assert DUPLICATE_ID_ERROR not in caplog.text


async def test_shared_watch_moves_when_the_first_account_drops_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi
) -> None:
    mum, dad = await _setup_two_accounts(hass, api)

    api.accounts["mum@example.com"] = ["111"]
    for _ in range(POLLS_BEFORE_REMOVAL):
        freezer.tick(POLL_INTERVAL)
        await _poll(hass, mum)
        await _poll(hass, dad)

    assert set(dad.runtime_data.coordinator.data) == {"222", "333"}
    assert _entry_of(hass, "222_battery") == dad.entry_id
    assert _battery_state(hass, "222") == "80"
    assert _device(hass, "222").config_entries == {dad.entry_id}
