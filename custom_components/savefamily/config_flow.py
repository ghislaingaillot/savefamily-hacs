from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_APP_ID, CONF_LOGINNAME, CONF_PASSWORD, CONF_REGION, DOMAIN, TITLE
from .core.async_client import SaveFamilyApiClient
from .core.protocol import DEFAULT_APP_ID, DEFAULT_REGION, REGIONS, SaveFamilyAuthError, SaveFamilyError


def _user_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_REGION, default=user_input.get(CONF_REGION, DEFAULT_REGION)): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(REGIONS),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_LOGINNAME, default=user_input.get(CONF_LOGINNAME, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
            vol.Optional(CONF_APP_ID, default=user_input.get(CONF_APP_ID, DEFAULT_APP_ID)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


def _reauth_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
        }
    )


class SaveFamilyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = f"{user_input[CONF_REGION]}:{user_input[CONF_LOGINNAME]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            client = SaveFamilyApiClient(
                async_get_clientsession(self.hass),
                region=user_input[CONF_REGION],
                loginname=user_input[CONF_LOGINNAME],
                password=user_input[CONF_PASSWORD],
                app_id=user_input.get(CONF_APP_ID, DEFAULT_APP_ID),
            )
            try:
                await client.async_login()
            except SaveFamilyAuthError:
                errors["base"] = "invalid_auth"
            except SaveFamilyError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"{TITLE} ({user_input[CONF_LOGINNAME]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_unsuccessful")

        if user_input is not None:
            data = {
                **self._reauth_entry.data,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            client = SaveFamilyApiClient(
                async_get_clientsession(self.hass),
                region=data[CONF_REGION],
                loginname=data[CONF_LOGINNAME],
                password=data[CONF_PASSWORD],
                app_id=data.get(CONF_APP_ID, DEFAULT_APP_ID),
            )
            try:
                await client.async_login()
            except SaveFamilyAuthError:
                errors["base"] = "invalid_auth"
            except SaveFamilyError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
        )
