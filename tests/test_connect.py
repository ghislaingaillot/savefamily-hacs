"""The Connect backend against a fake Connect gateway: session, positions and unexpected answers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.savefamily.connect.client import API_BASE, SaveFamilyConnectClient
from custom_components.savefamily.const import (
    BACKEND_CONNECT,
    CONF_BACKEND,
    CONF_LOGINNAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

DID = "860000000000001"
DEVICE_ID = "device-1"
POSITION = {"latitude": 48.85, "longitude": 2.35, "hpe": 20, "steps": 1200, "positionDate": 1_700_000_000_000}


def mock_gateway(
    aioclient_mock: AiohttpClientMocker,
    *,
    position: Any = POSITION,
    refresh_status: int = 200,
) -> None:
    """A Connect account with one watch, as the gateway answers it."""
    aioclient_mock.clear_requests()
    if refresh_status == 200:
        aioclient_mock.post(
            f"{API_BASE}/auth/refresh",
            json={"accessToken": "access", "refreshToken": "rotated", "expiresIn": 900},
        )
    else:
        aioclient_mock.post(f"{API_BASE}/auth/refresh", status=refresh_status, text='{"error": {}}')
    aioclient_mock.get(
        f"{API_BASE}/devices/",
        json={"items": [{"id": DEVICE_ID, "identificator": DID, "battery": 76, "type": "watch"}], "pages": 1},
    )
    aioclient_mock.get(
        f"{API_BASE}/devices/{DEVICE_ID}/positions/latest",
        json={} if position is None else {"item": position},
    )


def make_connect_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="parent@example.com",
        unique_id="connect:parent@example.com",
        data={CONF_BACKEND: BACKEND_CONNECT, CONF_LOGINNAME: "parent@example.com", CONF_REFRESH_TOKEN: "initial"},
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_connect_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, domain: str, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DID}_{key}")
    return hass.states.get(entity_id) if entity_id else None


async def test_setup_stores_the_rotated_refresh_token(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_gateway(aioclient_mock)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated"
    assert _state(hass, "sensor", "battery").state == "76"
    tracker = _state(hass, "device_tracker", "location")
    assert (tracker.attributes["latitude"], tracker.attributes["longitude"]) == (48.85, 2.35)


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_rejected_refresh_token_asks_to_log_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    mock_gateway(aioclient_mock, refresh_status=status)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_refresh_failing_on_the_server_side_is_retried(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    mock_gateway(aioclient_mock, refresh_status=status)
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.parametrize(
    "position",
    [None, {"latitude": 0, "longitude": 0, "steps": 0}, {"steps": 1300}, "unexpected"],
    ids=["no item", "zero", "no coordinates", "not an object"],
)
async def test_position_without_a_fix_keeps_the_last_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, position: Any
) -> None:
    mock_gateway(aioclient_mock)
    entry = await _setup(hass)

    mock_gateway(aioclient_mock, position=position)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    tracker = _state(hass, "device_tracker", "location")
    assert (tracker.attributes["latitude"], tracker.attributes["longitude"]) == (48.85, 2.35)
    assert _state(hass, "sensor", "steps").state == "1200"


async def test_non_json_answer_fails_the_poll_and_keeps_the_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_gateway(aioclient_mock)
    entry = await _setup(hass)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API_BASE}/devices/", text="<html>Maintenance</html>")
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert coordinator.data[DID].battery == 76


async def test_skipped_watch_is_listed_but_not_read(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_gateway(aioclient_mock)
    client = SaveFamilyConnectClient(async_get_clientsession(hass), email="parent@example.com", refresh_token="initial")

    states = await client.async_refresh_watch_states(skip={DID})

    assert states[DID].latitude is None
    assert not [call for call in aioclient_mock.mock_calls if call[1].path.endswith("/positions/latest")]


async def test_rotated_token_is_stored_when_the_caller_is_cancelled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    answering = asyncio.Event()
    release = asyncio.Event()

    async def slow_refresh(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        answering.set()
        await release.wait()
        return AiohttpClientMockResponse(
            method, url, json={"accessToken": "access", "refreshToken": "rotated", "expiresIn": 900}
        )

    aioclient_mock.post(f"{API_BASE}/auth/refresh", side_effect=slow_refresh)
    stored: list[str] = []
    client = SaveFamilyConnectClient(
        async_get_clientsession(hass), email="parent@example.com", refresh_token="initial", on_refresh_token=stored.append
    )

    poll = asyncio.create_task(client.async_refresh_watch_states())
    await answering.wait()
    # Unload or shutdown cancels the poll after the server has received the refresh.
    poll.cancel()
    release.set()
    await client.async_wait_for_token_refresh()

    assert poll.cancelled()
    assert stored == ["rotated"]
    assert client.refresh_token == "rotated"
