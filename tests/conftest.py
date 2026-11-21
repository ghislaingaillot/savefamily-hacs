"""Shared fixtures: Home Assistant with the integration and a fake SaveFamily API, no network."""

from __future__ import annotations

from collections.abc import Callable, Container, Generator
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
    """What the fake server answers: the watches of each account, their alarms, or errors."""

    def __init__(self) -> None:
        # Watches of every account, unless listed in accounts by login name.
        self.dids: list[str] = ["111", "222"]
        self.accounts: dict[str, list[str]] = {}
        self.alarms: dict[str, list[SaveFamilyAlarm]] = {}
        self.alarm_errors: dict[str, SaveFamilyError] = {}
        self.fail = False
        self.failing_accounts: set[str] = set()
        # (login name, did) of each position read.
        self.reads: list[tuple[str, str]] = []
        # Called with the did on each settings read.
        self.on_settings_read: Callable[[str], None] | None = None

    def add_alarm(self, did: str, alarm_type: str) -> None:
        alarms = self.alarms.setdefault(did, [])
        alarms.append(SaveFamilyAlarm(id=len(alarms) + 1, type=alarm_type, type_code=None, message="", created=None))


class FakeSaveFamilyClient:
    """Replaces SaveFamilyApiClient with the few calls a poll makes."""

    def __init__(self, api: FakeApi, region: str, loginname: str) -> None:
        self._api = api
        self.region = region
        self._loginname = loginname

    async def async_refresh_watch_states(
        self, previous: dict[str, SaveFamilyWatchState] | None = None, skip: Container[str] = ()
    ) -> dict[str, SaveFamilyWatchState]:
        if self._api.fail or self._loginname in self._api.failing_accounts:
            raise SaveFamilyError("server unreachable")
        states = {}
        for did in self._api.accounts.get(self._loginname, self._api.dids):
            watch = SaveFamilyWatch(did=did, did_id=f"id{did}", model="g36", nickname=f"Watch {did}", rolename="")
            if did in skip:
                states[did] = SaveFamilyWatchState(watch=watch)
                continue
            self._api.reads.append((self._loginname, did))
            states[did] = SaveFamilyWatchState(watch=watch, latitude=48.85, longitude=2.35, battery=80)
        return states

    async def async_find_alarms(self, did: str) -> list[SaveFamilyAlarm]:
        if did in self._api.alarm_errors:
            raise self._api.alarm_errors[did]
        return list(self._api.alarms.get(did, []))

    async def _async_not_served(self, did: str) -> Any:
        if self._api.on_settings_read is not None:
            self._api.on_settings_read(did)
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

    def _client(session: Any, *, region: str, loginname: str, **kwargs: Any) -> FakeSaveFamilyClient:
        return FakeSaveFamilyClient(fake, region, loginname)

    with patch("custom_components.savefamily.core.async_client.SaveFamilyApiClient", _client):
        yield fake


def make_entry(loginname: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=loginname,
        unique_id=f"europe:{loginname}",
        data={CONF_REGION: "europe", CONF_LOGINNAME: loginname, CONF_PASSWORD: "secret"},
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return make_entry("parent@example.com")
