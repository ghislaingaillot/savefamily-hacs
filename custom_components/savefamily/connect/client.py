"""Async client for the SaveFamily Connect REST gateway.

Connect is a separate platform from Classic (tgelec/myaqsh): plain JSON over HTTPS,
a JWT access token (15 minutes, sent as a cookie) and a refresh token that the server rotates
on every refresh, each time with a fresh 30-day window. Login needs an emailed code
once; after that the stored refresh token keeps the session alive while Home
Assistant keeps polling. See docs/CONNECT.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Container
from dataclasses import replace
from datetime import UTC, datetime
from json import loads as json_loads
from typing import Any

import aiohttp

from ..core.protocol import (
    SaveFamilyAuthError,
    SaveFamilyConnectionError,
    SaveFamilyError,
    SaveFamilyHTTPError,
    SaveFamilyResponseError,
    SaveFamilyWatch,
    SaveFamilyWatchState,
    coerce_accuracy,
    coerce_float,
    coerce_int,
)

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api-platform.pro.savefamily.app/gateway/api"
PORTAL_ORIGIN = "https://portal.savefamily.app"
ACCESS_COOKIE = "token"
DEFAULT_2FA_METHOD = "EMAIL"
LOGIN_PLATFORM = "web"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Refresh the access token a little before the server expires it.
TOKEN_EXPIRY_MARGIN = 60
# Access-token lifetime when a token response omits `expiresIn` (the server sends 900).
DEFAULT_ACCESS_LIFETIME = 900.0
# Refresh rejections that are not about the token: a timeout and rate limiting.
_TRANSIENT_CLIENT_ERRORS = (408, 429)
# Safety cap on `/devices/` pages; a family account has one.
MAX_DEVICE_PAGES = 20
# Position fields that identify nearby Wi-Fi networks; never kept in entity state.
_PRIVATE_POSITION_KEYS = ("networks", "message", "address")


class SaveFamilyConnectCodeError(SaveFamilyAuthError):
    """The emailed login code was rejected."""


class SaveFamilyConnectClient:
    """Connect client exposing the same surface the coordinator uses for Classic."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str,
        refresh_token: str | None = None,
        on_refresh_token: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self.email = email
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._twofa_token: str | None = None
        self._twofa_method = DEFAULT_2FA_METHOD
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def login_method(self) -> str:
        """Where the login code was sent (e.g. EMAIL), once the password step ran."""
        return self._twofa_method

    # --- login (config flow) -------------------------------------------------

    async def async_start_login(self, password: str) -> str:
        """Check email/password and have the server send a login code; return its method."""
        data = await self._request(
            "POST", "/auth/login", json={"email": self.email, "password": password}, auth=False
        )
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise SaveFamilyError("login response has no 2FA session")
        methods = [m for m in data.get("availableMethods") or [] if isinstance(m, dict)]
        default = next((m for m in methods if m.get("isDefault")), methods[0] if methods else {})
        self._twofa_token = token
        self._twofa_method = default.get("methodType") or DEFAULT_2FA_METHOD
        await self._request(
            "POST",
            "/auth/2fa/request-code",
            json={"token": token, "methodType": self._twofa_method},
            auth=False,
            bare_ok=True,
        )
        return self._twofa_method

    async def async_finish_login(self, code: str) -> None:
        """Exchange the emailed code for tokens."""
        if self._twofa_token is None:
            raise SaveFamilyError("login code requested before the password step")
        try:
            data = await self._request(
                "POST",
                "/auth/twofa/login",
                json={
                    "token": self._twofa_token,
                    "code": code.strip(),
                    "methodType": self._twofa_method,
                    "platform": LOGIN_PLATFORM,
                    "rememberMe": True,
                },
                auth=False,
            )
        except SaveFamilyAuthError as exc:
            raise SaveFamilyConnectCodeError(str(exc)) from exc
        self._store_tokens(data)

    # --- session ---------------------------------------------------------------

    async def _async_ensure_access_token(self) -> None:
        async with self._lock:
            if self._access_token and time.monotonic() < self._access_expires_at:
                return
            await self._async_refresh_access_token()

    async def _async_refresh_access_token(self) -> None:
        if not self._refresh_token:
            raise SaveFamilyAuthError("no Connect session; log in again")
        # The refresh and the storage of the rotated token run in one task that cancelling the caller
        # (unload, shutdown) does not interrupt: a token the server has rotated must reach the entry.
        if self._refresh_task is None:
            self._refresh_task = asyncio.ensure_future(self._async_do_refresh())
            self._refresh_task.add_done_callback(self._refresh_done)
        await asyncio.shield(self._refresh_task)

    async def _async_do_refresh(self) -> None:
        try:
            data = await self._request(
                "POST", "/auth/refresh", json={"refreshToken": self._refresh_token}, auth=False
            )
        except SaveFamilyHTTPError as exc:
            # The server answers a rejected refresh token with 401; any other client error means
            # the same, while a timeout, rate limiting or a server error is retried at the next poll.
            if 400 <= exc.status < 500 and exc.status not in _TRANSIENT_CLIENT_ERRORS:
                raise SaveFamilyAuthError(f"refresh token rejected: {exc}") from exc
            raise
        self._store_tokens(data)

    def _refresh_done(self, task: asyncio.Task[None]) -> None:
        self._refresh_task = None
        if not task.cancelled():
            # Retrieved here too, for when the caller that started it was cancelled.
            task.exception()

    async def async_wait_for_token_refresh(self) -> None:
        """Let a refresh in progress finish and store its token (unload, shutdown)."""
        if (task := self._refresh_task) is not None:
            await asyncio.wait([task])

    def _store_tokens(self, data: dict[str, Any]) -> None:
        access = data.get("accessToken")
        refresh = data.get("refreshToken")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise SaveFamilyError("token response is missing accessToken or refreshToken")
        lifetime = coerce_float(data.get("expiresIn"))
        if lifetime is None or lifetime <= 0:
            lifetime = DEFAULT_ACCESS_LIFETIME
        self._access_token = access
        self._access_expires_at = time.monotonic() + max(lifetime - TOKEN_EXPIRY_MARGIN, 0)
        if refresh != self._refresh_token:
            self._refresh_token = refresh
            if self._on_refresh_token is not None:
                self._on_refresh_token(refresh)

    # --- data ------------------------------------------------------------------

    async def async_refresh_watch_states(
        self,
        previous: dict[str, SaveFamilyWatchState] | None = None,
        skip: Container[str] = (),
    ) -> dict[str, SaveFamilyWatchState]:
        """State of every watch of the account; those in skip are listed but not read."""
        previous = previous or {}
        states: dict[str, SaveFamilyWatchState] = {}
        for device in await self._async_list_devices():
            if not isinstance(device, dict) or not device.get("id"):
                continue
            watch = _watch_from_device(device)
            if watch.did in skip:
                states[watch.did] = SaveFamilyWatchState(watch=watch)
                continue
            try:
                # The device list already proved the session, so a 401/403 here is about
                # this watch only (e.g. limited sharing) and must not end the session.
                latest = await self._authed(
                    "GET", f"/devices/{watch.did_id}/positions/latest", retry_auth=False
                )
            except SaveFamilyError as exc:
                _LOGGER.debug("Latest position of %s failed: %s", watch.did, exc)
                latest = {}
            states[watch.did] = _state_from_position(watch, device, latest.get("item"), previous.get(watch.did))
        if not states:
            # Like Classic: an empty list keeps the last data instead of removing every device.
            raise SaveFamilyResponseError(None, "no watches were found on this SaveFamily Connect account", {})
        return states

    async def _async_list_devices(self) -> list[Any]:
        """All pages of `GET /devices/` (`{total, items, page, pages}`)."""
        items: list[Any] = []
        page = 1
        while True:
            data = await self._authed("GET", "/devices/" if page == 1 else f"/devices/?page={page}")
            items.extend(data.get("items") or [])
            pages = coerce_int(data.get("pages")) or 1
            if page >= min(pages, MAX_DEVICE_PAGES) or not data.get("items"):
                return items
            page += 1

    async def async_find_alarms(self, did: str) -> list:
        """Connect alarms are not mapped yet."""
        return []

    async def async_send_command(
        self, did: str, command: str, *params: str, with_model: bool = False
    ) -> None:
        raise SaveFamilyError("Watch commands are not supported on SaveFamily Connect yet")

    async def _authed(
        self, method: str, path: str, *, retry_auth: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        await self._async_ensure_access_token()
        try:
            return await self._request(method, path, **kwargs)
        except SaveFamilyAuthError:
            if not retry_auth:
                raise
            # Access token revoked early: one refresh and retry.
            async with self._lock:
                await self._async_refresh_access_token()
            return await self._request(method, path, **kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
        bare_ok: bool = False,
    ) -> dict[str, Any]:
        """JSON body of a 2xx answer; bare_ok accepts a non-JSON one (an action answering "OK")."""
        request_headers = {
            "Accept": "application/json",
            "Origin": PORTAL_ORIGIN,
            "Referer": f"{PORTAL_ORIGIN}/",
            **(headers or {}),
        }
        if auth and self._access_token:
            # The gateway reads the access token from the portal's `token` cookie.
            request_headers["Authorization"] = f"Bearer {self._access_token}"
            request_headers["Cookie"] = f"{ACCESS_COOKIE}={self._access_token}"
        try:
            async with self._session.request(
                method,
                f"{API_BASE}{path}",
                json=json,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                text = await response.text(errors="replace")
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SaveFamilyConnectionError(f"{method} {path}: {exc}") from exc

        _LOGGER.debug("Connect %s %s -> %s", method, path, status)
        if status in (401, 403):
            raise SaveFamilyAuthError(f"{method} {path}: HTTP {status} {_error_message(text)}")
        if status >= 400:
            raise SaveFamilyHTTPError(status, f"{method} {path}: {_error_message(text)}")
        try:
            payload = json_loads(text)
        except ValueError:
            if bare_ok:
                return {}
            # A maintenance page or an empty body: not an answer to read data from.
            raise SaveFamilyResponseError(None, f"{method} {path}: not JSON: {_error_message(text)}", {}) from None
        return payload if isinstance(payload, dict) else {"items": payload}


def _error_message(text: str) -> str:
    return text[:200]


def _timestamp(value: Any) -> datetime | None:
    """Connect timestamps are epoch milliseconds; anything unparseable is None."""
    millis = coerce_float(value)
    if millis is None or millis <= 0:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _watch_from_device(device: dict[str, Any]) -> SaveFamilyWatch:
    return SaveFamilyWatch(
        did=str(device.get("identificator") or device["id"]),
        did_id=str(device["id"]),
        model=str(device.get("type") or ""),
        nickname=str(device.get("carrierName") or ""),
        rolename="",
        watch_type=str(device.get("protocol") or ""),
    )


def _state_from_position(
    watch: SaveFamilyWatch,
    device: dict[str, Any],
    position: Any,
    previous: SaveFamilyWatchState | None,
) -> SaveFamilyWatchState:
    fields = {"battery": coerce_int(device.get("battery"))}
    if not isinstance(position, dict):
        position = {}
    latitude = coerce_float(position.get("latitude"))
    longitude = coerce_float(position.get("longitude"))
    if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
        # No position, or the (0, 0) "no fix" sentinel: keep the last one, like Classic, rather
        # than clearing the tracker or placing the watch in the Gulf of Guinea.
        return replace(previous or SaveFamilyWatchState(watch=watch), watch=watch, **fields)
    return SaveFamilyWatchState(
        watch=watch,
        latitude=latitude,
        longitude=longitude,
        accuracy=coerce_accuracy(position.get("hpe")),
        step_count=coerce_int(position.get("steps")),
        last_fix=_timestamp(position.get("positionDate")),
        raw_position={k: v for k, v in position.items() if k not in _PRIVATE_POSITION_KEYS},
        **fields,
    )
