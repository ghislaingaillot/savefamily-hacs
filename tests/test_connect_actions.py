"""Classic-only actions on targets that include Connect watches."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.savefamily.const import ATTR_PHONE_NUMBER, DOMAIN, SERVICE_CALL_BACK
from custom_components.savefamily.core.protocol import COMMAND_CALL_BACK

from .conftest import FakeApi, make_entry
from .test_connect import DID, _setup, _state, mock_gateway


async def test_classic_action_on_an_area_skips_connect_watches(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, api: FakeApi
) -> None:
    classic = make_entry("parent@example.com")
    classic.add_to_hass(hass)
    assert await hass.config_entries.async_setup(classic.entry_id)
    mock_gateway(aioclient_mock)
    await _setup(hass)
    device_reg = dr.async_get(hass)
    devices = {did: device_reg.async_get_device(identifiers={(DOMAIN, did)}) for did in ("111", DID)}
    area = ar.async_get(hass).async_create("Kids")
    for device in devices.values():
        device_reg.async_update_device(device.id, area_id=area.id)

    await hass.services.async_call(
        DOMAIN, SERVICE_CALL_BACK, {"area_id": area.id, ATTR_PHONE_NUMBER: "+33639980001"}, blocking=True
    )
    assert api.commands == [("parent@example.com", "111", COMMAND_CALL_BACK)]

    # Named in the target, a Connect watch makes the action fail, as does an area with only Connect watches.
    device_reg.async_update_device(devices["111"].id, area_id=None)
    for target in (
        {"device_id": [devices["111"].id, devices[DID].id]},
        {"entity_id": _state(hass, "sensor", "battery").entity_id},
        {"area_id": area.id},
    ):
        with pytest.raises(HomeAssistantError, match="not supported on SaveFamily Connect"):
            await hass.services.async_call(
                DOMAIN, SERVICE_CALL_BACK, {**target, ATTR_PHONE_NUMBER: "+33639980001"}, blocking=True
            )
    assert len(api.commands) == 1
