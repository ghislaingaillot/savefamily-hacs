"""The SOS automation of the README runs once for each new SOS."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from .conftest import FakeApi
from .test_init import _poll, _setup


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
