"""Alarms: the README SOS automation, and no alarm lost when a poll fails halfway."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_mock_service,
)

from custom_components.savefamily.const import DOMAIN, EVENT_ALARM
from custom_components.savefamily.core.protocol import SaveFamilyAuthError

from .conftest import FakeApi
from .test_init import POLLS_BEFORE_REMOVAL, _poll, _setup


async def test_readme_sos_automation_runs_once_per_sos(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    calls = async_mock_service(hass, "test", "notify")
    # The README example, in the automation syntax of Home Assistant 2024.5.
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {"platform": "event", "event_type": "savefamily_alarm", "event_data": {"type": "sos"}},
                "action": {"service": "test.notify", "data": {"message": "SOS triggered on {{ trigger.event.data.watch }}!"}},
            }
        },
    )
    # Alarms already present at startup are not replayed.
    api.add_alarm("111", "sos")
    await _setup(hass, config_entry)

    # Two SOS in a row.
    api.add_alarm("111", "sos")
    await _poll(hass, config_entry)
    api.add_alarm("111", "sos")
    await _poll(hass, config_entry)
    # An outage, then a poll without any new alarm.
    api.fail = True
    await _poll(hass, config_entry)
    api.fail = False
    await _poll(hass, config_entry)
    # Other alarm types do not run it.
    api.add_alarm("111", "low_battery")
    await _poll(hass, config_entry)

    assert [call.data["message"] for call in calls] == ["SOS triggered on Watch 111!"] * 2


async def test_alarms_are_not_lost_when_another_watch_fails(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_ALARM)
    await _setup(hass, config_entry)

    # Watch 111 is read first: its SOS must survive the error on watch 222.
    api.add_alarm("111", "sos")
    api.alarm_errors["222"] = SaveFamilyAuthError("session expired")
    await _poll(hass, config_entry)
    assert events == []

    del api.alarm_errors["222"]
    await _poll(hass, config_entry)
    await _poll(hass, config_entry)

    assert [(event.data["did"], event.data["type"]) for event in events] == [("111", "sos")]


async def test_alarm_of_a_watch_coming_back_reaches_its_entity(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    api.dids = ["111"]
    await _poll(hass, config_entry, POLLS_BEFORE_REMOVAL, freezer)
    assert er.async_get(hass).async_get_entity_id("event", DOMAIN, "222_alarm") is None

    # The watch comes back with a SOS: its alarm entity, created by that poll, shows it.
    api.dids = ["111", "222"]
    api.add_alarm("222", "sos")
    await _poll(hass, config_entry, freezer=freezer)

    alarm = hass.states.get(er.async_get(hass).async_get_entity_id("event", DOMAIN, "222_alarm"))
    assert alarm.attributes["event_type"] == "sos"
