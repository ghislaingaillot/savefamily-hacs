from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .connect.client import SaveFamilyConnectClient, SaveFamilyConnectCodeError
from .const import (
    BACKEND_CLASSIC,
    BACKEND_CONNECT,
    CONF_APP_ID,
    CONF_BACKEND,
    CONF_CODE,
    CONF_LOGINNAME,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_RESEND,
    DOMAIN,
    TITLE,
)
from .core.async_client import SaveFamilyApiClient
from .core.protocol import (
    DEFAULT_APP_ID,
    DEFAULT_REGION,
    REGIONS,
    SaveFamilyAuthError,
    SaveFamilyError,
    SaveFamilyUpgradeRequiredError,
)

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)


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


async def _async_try_login(client: SaveFamilyApiClient) -> str | None:
    """Log in and return a config-flow error key, or None on success."""
    try:
        await client.async_login()
    except SaveFamilyUpgradeRequiredError as exc:
        _LOGGER.warning("SaveFamily login blocked by server: %s", exc)
        return "upgrade_required"
    except SaveFamilyAuthError as exc:
        _LOGGER.warning("SaveFamily login rejected (region %s): %s", client.region.name, exc)
        return "invalid_auth"
    except SaveFamilyError as exc:
        _LOGGER.warning("SaveFamily login failed: %s", exc)
        return "cannot_connect"
    return None


def _reauth_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
        }
    )


def _connect_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_LOGINNAME, default=user_input.get(CONF_LOGINNAME, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
        }
    )


def _connect_code_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_CODE, default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="one-time-code")
            ),
            vol.Optional(CONF_RESEND, default=False): BooleanSelector(),
        }
    )


async def _async_try_connect_step(step) -> str | None:
    """Run one Connect login step and return a config-flow error key, or None on success."""
    try:
        await step
    except SaveFamilyConnectCodeError as exc:
        _LOGGER.warning("SaveFamily Connect login code rejected: %s", exc)
        return "invalid_code"
    except SaveFamilyAuthError as exc:
        _LOGGER.warning("SaveFamily Connect login rejected: %s", exc)
        return "connect_invalid_auth"
    except SaveFamilyError as exc:
        _LOGGER.warning("SaveFamily Connect login failed: %s", exc)
        return "cannot_connect"
    return None


class SaveFamilyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._connect_client: SaveFamilyConnectClient | None = None
        self._connect_session: aiohttp.ClientSession | None = None
        # Held in memory only while the flow runs, to resend the code; never stored in the entry.
        self._connect_password: str | None = None

    @callback
    def async_remove(self) -> None:
        """Release the flow's private session when the flow finishes, aborts or times out."""
        if self._connect_session is not None:
            # Home Assistant owns the session's connector: detach instead of closing it.
            self._connect_session.detach()
        self._connect_session = None
        self._connect_password = None
        super().async_remove()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="user", menu_options=[BACKEND_CONNECT, BACKEND_CLASSIC])

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_LOGINNAME].strip()
            await self.async_set_unique_id(f"{BACKEND_CONNECT}:{email.lower()}")
            self._abort_if_unique_id_configured()
            if error := await self._async_start_connect_login(email, user_input[CONF_PASSWORD]):
                errors["base"] = error
            else:
                return await self.async_step_connect_code()

        return self.async_show_form(
            step_id="connect",
            data_schema=_connect_schema(user_input),
            errors=errors,
        )

    async def _async_start_connect_login(self, email: str, password: str) -> str | None:
        if self._connect_session is None:
            # A private cookie-less session: the account's tokens must not land in a shared jar.
            self._connect_session = async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar())
        client = SaveFamilyConnectClient(self._connect_session, email=email)
        if error := await _async_try_connect_step(client.async_start_login(password)):
            # Keep the previous login: its code is still valid when no new one was sent.
            return error
        self._connect_client = client
        self._connect_password = password
        return None

    async def async_step_connect_code(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask for the code SaveFamily emails on every new login; the password is not stored."""
        errors: dict[str, str] = {}
        client = self._connect_client
        if client is None:
            return self.async_abort(reason="reauth_unsuccessful")

        if user_input is not None and user_input.get(CONF_RESEND):
            # Start a new login: the server emails a fresh code and the old one stops working.
            if error := await self._async_start_connect_login(client.email, self._connect_password or ""):
                errors["base"] = error
            client = self._connect_client
        elif user_input is not None and not (code := user_input.get(CONF_CODE, "").strip()):
            errors["base"] = "invalid_code"
        elif user_input is not None:
            if error := await _async_try_connect_step(client.async_finish_login(code)):
                errors["base"] = error
            else:
                data = {
                    CONF_BACKEND: BACKEND_CONNECT,
                    CONF_LOGINNAME: client.email,
                    CONF_REFRESH_TOKEN: client.refresh_token,
                }
                if self._reauth_entry is not None:
                    self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                    await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
                return self.async_create_entry(title=f"{TITLE} Connect ({client.email})", data=data)

        return self.async_show_form(
            step_id="connect_code",
            data_schema=_connect_code_schema(),
            errors=errors,
            description_placeholders={"email": client.email, "method": client.login_method.lower()},
        )

    async def async_step_classic(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_LOGINNAME] = user_input[CONF_LOGINNAME].strip()
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
            if error := await _async_try_login(client):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"{TITLE} ({user_input[CONF_LOGINNAME]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="classic",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_unsuccessful")

        if user_input is not None and self._reauth_entry.data.get(CONF_BACKEND) == BACKEND_CONNECT:
            email = self._reauth_entry.data[CONF_LOGINNAME]
            if error := await self._async_start_connect_login(email, user_input[CONF_PASSWORD]):
                errors["base"] = error
            else:
                return await self.async_step_connect_code()
        elif user_input is not None:
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
            if error := await _async_try_login(client):
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
        )
