"""Shared fixtures: Home Assistant with the integration and a fake SaveFamily API, no network."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.savefamily.const import (
    CONF_LOGINNAME,
    CONF_PASSWORD,
    CONF_REGION,
    DOMAIN,
)
from custom_components.savefamily.core.protocol import (
    SaveFamilyAlarm,
    SaveFamilyError,
    SaveFamilyWatch,
    SaveFamilyWatchState,
)

pytest_plugins = "pytest_homeassistant_custom_component"


class FakeApi:
    """What the fake server answers: the watches of the account, or an error."""

    def __init__(self) -> None:
        self.dids: list[str] = ["111", "222"]
        self.alarms: dict[str, list[SaveFamilyAlarm]] = {}
        self.fail = False

    def add_alarm(self, did: str, alarm_type: str) -> None:
        alarms = self.alarms.setdefault(did, [])
        alarms.append(SaveFamilyAlarm(id=len(alarms) + 1, type=alarm_type, type_code=None, message="", created=None))


class FakeSaveFamilyClient:
    """Replaces SaveFamilyApiClient with the few calls a poll makes."""

    def __init__(self, api: FakeApi, region: str) -> None:
        self._api = api
        self.region = region

    async def async_refresh_watch_states(
        self, previous: dict[str, SaveFamilyWatchState] | None = None
    ) -> dict[str, SaveFamilyWatchState]:
        if self._api.fail:
            raise SaveFamilyError("server unreachable")
        return {
            did: SaveFamilyWatchState(
                watch=SaveFamilyWatch(did=did, did_id=f"id{did}", model="g36", nickname=f"Watch {did}", rolename=""),
                latitude=48.85,
                longitude=2.35,
                battery=80,
            )
            for did in self._api.dids
        }

    async def async_find_alarms(self, did: str) -> list[SaveFamilyAlarm]:
        return list(self._api.alarms.get(did, []))

    async def _async_not_served(self, did: str) -> Any:
        raise SaveFamilyError("not served by the fake API")

    # Settings failures never block location updates.
    async_find_set_info = _async_not_served
    async_find_safe_zones = _async_not_served
    async_find_latest_photo = _async_not_served


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def api() -> Generator[FakeApi]:
    fake = FakeApi()

    def _client(session: Any, *, region: str, **kwargs: Any) -> FakeSaveFamilyClient:
        return FakeSaveFamilyClient(fake, region)

    with patch("custom_components.savefamily.core.async_client.SaveFamilyApiClient", _client):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="parent@example.com",
        unique_id="europe:parent@example.com",
        data={CONF_REGION: "europe", CONF_LOGINNAME: "parent@example.com", CONF_PASSWORD: "secret"},
    )
