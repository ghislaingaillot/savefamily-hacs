from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .protocol import (
    ALARM_FETCH_COUNT,
    COMMAND_REQUEST_LOCATION,
    COMMAND_SOS_NUMBERS,
    COMMAND_WORK_MODE,
    DEFAULT_APP_ID,
    DEFAULT_CLIENT_FLAG,
    DEFAULT_CLIENT_VERSION,
    DEFAULT_IS_IPHONE,
    DEFAULT_LANGUAGE,
    DEFAULT_SIGN_FLAG,
    DEVICE_OFFLINE_MESSAGE,
    DEVICE_OFFLINE_STATUS,
    MAX_SOS_NUMBERS,
    POSITION_HISTORY_DATE_FORMAT,
    REGIONS,
    SUCCESS_STATUSES,
    WORK_MODES,
    DndPeriod,
    PhotoWallItem,
    SafeZone,
    SaveFamilyAlarm,
    SaveFamilyAuthError,
    SaveFamilyConnectionError,
    SaveFamilyError,
    SaveFamilyResponseError,
    SaveFamilySettings,
    SaveFamilyUpgradeRequiredError,
    SaveFamilyWatch,
    SaveFamilyWatchState,
    build_watch_index,
    build_watch_state,
    coerce_int,
    command_sendurl,
    compute_sign,
    dnd_schedule_fields,
    extract_alarms,
    extract_latest_photo,
    extract_position_history,
    extract_safe_zones,
    hash_password,
    is_login_timeout_response,
    photo_wall_filename,
    supports_dnd_schedule,
)
from .transport import (
    ENCRYPT_INDEX_HEADER,
    create_ssl_context,
    decrypt_response,
    encrypt_request,
)

_LOGGER = logging.getLogger(__name__)


class SaveFamilyApiClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        region: str,
        loginname: str,
        password: str,
        app_id: str = DEFAULT_APP_ID,
        language: str = DEFAULT_LANGUAGE,
        request_timeout: float = 20.0,
    ) -> None:
        if region not in REGIONS:
            choices = ", ".join(sorted(REGIONS))
            raise ValueError(f"unknown region {region!r}; choose one of: {choices}")

        self.session = session
        self.region = REGIONS[region]
        self.loginname = loginname.strip()
        self.password = password
        self.app_id = app_id
        self.language = language
        self.request_timeout = request_timeout
        self.ssl_context: ssl.SSLContext | None = None

        self.session_id: str | None = None
        self.user_id: int | None = None
        self._watches: dict[str, SaveFamilyWatch] = {}

    @property
    def watches(self) -> dict[str, SaveFamilyWatch]:
        return dict(self._watches)

    async def async_refresh_watch_states(
        self,
        previous: dict[str, SaveFamilyWatchState] | None = None,
    ) -> dict[str, SaveFamilyWatchState]:
        login_payload = await self.async_login()
        watches = build_watch_index(login_payload, self.user_id)
        if self.user_id is not None:
            device_payload = await self.async_find_device_list_by_user_id()
            _LOGGER.debug("v2_findDeviceListByUserId response: %s", device_payload)
            device_watches = build_watch_index(device_payload, self.user_id)
            if not watches:
                watches = device_watches
            elif device_watches:
                watches = self._merge_watches(watches, device_watches)

        if not watches:
            raise SaveFamilyResponseError(None, "login succeeded but no watches were discovered", login_payload)

        self._watches = watches
        previous = previous or {}
        states: dict[str, SaveFamilyWatchState] = {}
        for did, watch in watches.items():
            try:
                response = await self.async_find_last_position(watch)
            except SaveFamilyAuthError:
                raise
            except SaveFamilyResponseError as exc:
                states[did] = build_watch_state(watch, exc.payload, previous.get(did))
            except SaveFamilyError as exc:
                states[did] = build_watch_state(
                    watch,
                    {"status": -1, "message": str(exc), "data": []},
                    previous.get(did),
                )
            else:
                states[did] = build_watch_state(watch, response, previous.get(did))

            try:
                signal = await self.async_find_gsm_signal(did)
            except SaveFamilyAuthError:
                raise
            except SaveFamilyError as exc:
                _LOGGER.debug("findDeviceGSMSignal failed for %s: %s", did, exc)
                previous_state = previous.get(did)
                signal = previous_state.signal if previous_state else None
            states[did] = replace(states[did], signal=signal)

            state = states[did]
            if state.step_count is None and state.raw_position:
                _LOGGER.debug(
                    "No step count found for %s (model=%s); raw position data: %s",
                    did,
                    watch.model,
                    state.raw_position,
                )
        return states

    async def async_request_location(self, did: str) -> dict[str, Any]:
        return await self.async_send_command(did, COMMAND_REQUEST_LOCATION, with_model=True)

    async def async_send_command(
        self,
        did: str,
        command: str,
        *params: str,
        with_model: bool = False,
    ) -> dict[str, Any]:
        """Send a v2_sendOrder command; the watch must be online to acknowledge it."""
        watch = await self._async_ensure_watch(did, require_model=with_model)
        response = await self._async_send_order(
            command_sendurl(watch.did, command, *params, model=watch.model if with_model else None)
        )
        if is_login_timeout_response(response):
            await self._async_reauthenticate()
            watch = await self._async_ensure_watch(did, require_model=with_model)
            response = await self._async_send_order(
                command_sendurl(watch.did, command, *params, model=watch.model if with_model else None)
            )
        self._ensure_command_success(response)
        return response

    async def async_find_alarms(self, did: str) -> list[SaveFamilyAlarm]:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_findAlarmInfo"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did": watch.did,
                        "did_id": watch.did_id,
                        "flag": 0,
                        "count": ALARM_FETCH_COUNT,
                        "createtime": "",
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        # Status 2 ("No new data") is a valid empty result.
        self._ensure_status(response, SUCCESS_STATUSES | {2})
        return extract_alarms(response)

    async def async_find_gsm_signal(self, did: str) -> int | None:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "POST",
                "/S10APP/findDeviceGSMSignal",
                data=self._signed_params(
                    {
                        "sid": self.session_id,
                        "language": self.language,
                        "user_id": self.user_id,
                        "did": watch.did,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES)
        return coerce_int(response.get("signal"))

    async def async_find_safe_zones(self, did: str) -> list[SafeZone]:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_new_findRailInfo"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did_id": watch.did_id,
                        "did": watch.did,
                        "loginname": self.loginname,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES | {2})
        return extract_safe_zones(response)

    async def async_find_latest_photo(self, did: str) -> PhotoWallItem | None:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_findPictrueDoorInfo"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did": watch.did,
                        "max_id": 0,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES | {2})
        return extract_latest_photo(response)

    async def async_download_photo(self, did: str, filename: str) -> bytes:
        watch = await self._async_ensure_watch(did)
        return await self._request_bytes(
            self._session_path("/S10APP/v2_downloadPictrueDoor"),
            self._signed_params({"did": watch.did, "filename": photo_wall_filename(filename)}),
        )

    async def async_find_position_history(
        self,
        did: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_findPositionRecordByOneday"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did_id": watch.did_id,
                        "did": watch.did,
                        "ksdate": start.strftime(POSITION_HISTORY_DATE_FORMAT),
                        "jsdate": end.strftime(POSITION_HISTORY_DATE_FORMAT),
                        "checkWifiOpen": 1,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES | {2})
        return extract_position_history(response)

    async def async_set_work_mode(self, did: str, settings: SaveFamilySettings, mode: str) -> None:
        """Apply on the watch (D1), then persist like WorkModeActivity does."""
        interval = WORK_MODES[mode]
        await self.async_send_command(did, COMMAND_WORK_MODE, str(interval), with_model=True)
        await self._async_update_settings(did, settings, {"upinterval": interval})

    async def async_set_sos_numbers(self, did: str, settings: SaveFamilySettings, numbers: Sequence[str]) -> None:
        """Apply on the watch (D7), then persist like SOSActivity does."""
        if len(numbers) > MAX_SOS_NUMBERS:
            raise SaveFamilyError(f"at most {MAX_SOS_NUMBERS} SOS numbers are supported, got {len(numbers)}")
        slots = [*numbers, *[""] * (MAX_SOS_NUMBERS - len(numbers))]
        await self.async_send_command(did, COMMAND_SOS_NUMBERS, *slots)
        await self._async_update_settings(
            did,
            settings,
            {f"sosnumber{index}": number for index, number in enumerate(slots, start=1)},
        )

    async def _async_update_settings(
        self,
        did: str,
        settings: SaveFamilySettings,
        fields: dict[str, Any],
    ) -> None:
        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_upSetInfo"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did_id": watch.did_id,
                        "did": watch.did,
                        "id": settings.setting_id,
                        **fields,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES)

    async def async_login(self) -> dict[str, Any]:
        payload = self._signed_params(
            {
                "language": self.language,
                "appid": self.app_id,
                "password": hash_password(self.password),
                "loginname": self.loginname,
                "flag": DEFAULT_CLIENT_FLAG,
                "version": DEFAULT_CLIENT_VERSION,
                "isIPHONE": DEFAULT_IS_IPHONE,
            }
        )
        response = await self._request_json("POST", "/app/public/S10APP/v2_new_userLogin2", data=payload)
        self._ensure_login_success(response)

        sid = response.get("sid")
        if not isinstance(sid, str) or not sid:
            raise SaveFamilyResponseError(None, "login succeeded but no sid was returned", response)

        self.session_id = sid
        users = response.get("data")
        if isinstance(users, list) and users and isinstance(users[0], dict):
            user_id = users[0].get("id")
            if isinstance(user_id, int):
                self.user_id = user_id

        self._watches = build_watch_index(response, self.user_id)
        return response

    async def async_find_device_list_by_user_id(self) -> dict[str, Any]:
        if self.user_id is None:
            raise SaveFamilyError("user_id is required before fetching device metadata")

        payload = self._signed_params(
            {
                "language": self.language,
                "user_id": self.user_id,
                "loginname": self.loginname,
            }
        )
        response = await self._request_json("GET", "/app/public/S10APP/v2_findDeviceListByUserId", params=payload)
        self._ensure_status(response, SUCCESS_STATUSES)
        return response

    async def async_find_last_position(self, watch: SaveFamilyWatch) -> dict[str, Any]:
        if not watch.did_id:
            raise SaveFamilyError(f"did_id is required for watch {watch.did}")

        response = await self._async_find_last_position_once(watch)
        if is_login_timeout_response(response):
            await self._async_reauthenticate()
            refreshed = self._watches.get(watch.did, watch)
            response = await self._async_find_last_position_once(refreshed)
        self._ensure_status(response, SUCCESS_STATUSES | {2})
        response.setdefault("data", [])
        return response

    async def async_find_set_info(self, did: str) -> dict[str, Any]:
        """Fetch shared settings, including DND, SOS numbers and SMS alerts."""

        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            return await self._request_json(
                "GET",
                self._session_path("/S10APP/v2_findSetInfo"),
                params=self._signed_params(
                    {
                        "language": self.language,
                        "did_id": watch.did_id,
                        "did": watch.did,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, SUCCESS_STATUSES)
        return response

    async def async_set_dnd_schedule(self, did: str, periods: Sequence[DndPeriod]) -> dict[str, Any]:
        """Replace the DC == 2 schedule, clearing unused slots."""
        fields = dnd_schedule_fields(periods)

        async def send(watch: SaveFamilyWatch) -> dict[str, Any]:
            if not supports_dnd_schedule(watch.config):
                raise SaveFamilyError("DND schedule writing requires a watch advertising DC:2")
            return await self._request_json(
                "POST",
                "/S10APP/upNewDndSetInfo",
                data=self._signed_params(
                    {
                        "sid": self.session_id,
                        "did": watch.did,
                        "did_id": watch.did_id,
                        "language": self.language,
                        **fields,
                    }
                ),
            )

        response = await self._async_watch_request(did, send)
        self._ensure_status(response, {1})
        return response

    async def _async_watch_request(
        self,
        did: str,
        send: Callable[[SaveFamilyWatch], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Send with current watch metadata, retrying an expired session once."""
        response = await send(await self._async_ensure_watch(did))
        if is_login_timeout_response(response):
            await self._async_reauthenticate()
            response = await send(await self._async_ensure_watch(did))
        return response

    async def _async_ensure_watch(self, did: str, *, require_model: bool = False) -> SaveFamilyWatch:
        if did not in self._watches or not self.session_id:
            await self.async_login()
        if did not in self._watches and self.user_id is not None:
            device_payload = await self.async_find_device_list_by_user_id()
            self._watches = self._merge_watches(
                self._watches, build_watch_index(device_payload, self.user_id)
            )

        watch = self._watches.get(did)
        if watch is None:
            raise SaveFamilyError(f"unknown watch {did}")
        if require_model and not watch.model:
            raise SaveFamilyError(f"device model is required for {did}")
        return watch

    async def _async_reauthenticate(self) -> None:
        await self.async_login()
        if self.user_id is None:
            return
        device_payload = await self.async_find_device_list_by_user_id()
        self._watches = self._merge_watches(
            self._watches, build_watch_index(device_payload, self.user_id)
        )

    async def _async_send_order(self, sendurl: str) -> dict[str, Any]:
        payload = self._signed_params(
            {
                "sid": self.session_id,
                "language": self.language,
                "sendurl": sendurl,
            }
        )
        return await self._request_json("POST", self._session_path("/S10APP/v2_sendOrder"), data=payload)

    async def _async_find_last_position_once(self, watch: SaveFamilyWatch) -> dict[str, Any]:
        payload = self._signed_params(
            {
                "language": self.language,
                "did_id": watch.did_id,
                "did": watch.did,
                "id": "",
            }
        )
        return await self._request_json(
            "GET",
            self._session_path("/S10APP/v2_findLastPosition"),
            params=payload,
        )

    @staticmethod
    def _merge_watches(
        primary: dict[str, SaveFamilyWatch],
        secondary: dict[str, SaveFamilyWatch],
    ) -> dict[str, SaveFamilyWatch]:
        merged = dict(primary)
        for did, extra in secondary.items():
            current = merged.get(did)
            if current is None:
                merged[did] = extra
                continue
            merged[did] = SaveFamilyWatch(
                did=current.did,
                did_id=current.did_id or extra.did_id,
                model=current.model or extra.model,
                nickname=current.nickname or extra.nickname,
                rolename=current.rolename or extra.rolename,
                user_id=current.user_id if current.user_id is not None else extra.user_id,
                config=current.config or extra.config,
                is_esim=current.is_esim or extra.is_esim,
                watch_type=current.watch_type or extra.watch_type,
            )
        return merged

    def _session_path(self, suffix: str) -> str:
        if not self.session_id:
            raise SaveFamilyError("session_id is required; call async_login() first")
        return f"/app/{self.session_id}{suffix}"

    def _signed_params(self, params: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if value == "":
                normalized[key] = ""
                continue
            normalized[key] = str(value)

        normalized.setdefault("timestamppp", str(round(datetime.now(tz=UTC).timestamp() * 1000)))
        normalized.setdefault("sign_flag", DEFAULT_SIGN_FLAG)
        normalized["sign"] = compute_sign(normalized)
        return normalized

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raw, _ = await self._request_raw(method, path, params=params, data=data)
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SaveFamilyResponseError(None, f"server did not return JSON: {text[:500]}", {}) from exc

        if not isinstance(payload, dict):
            raise SaveFamilyResponseError(None, "server returned a non-object JSON payload", {"payload": payload})
        return decrypt_response(payload)

    async def _request_bytes(self, path: str, params: dict[str, str]) -> bytes:
        """GET a binary file; a JSON body means the server returned an error instead."""
        raw, content_type = await self._request_raw("GET", path, params=params, accept="*/*")
        if content_type != "application/json" and not raw.lstrip().startswith((b"{", b"[")):
            return raw
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, dict):
            payload = decrypt_response(payload)
            status = coerce_int(payload.get("status", payload.get("code")))
            message = str(payload.get("message", payload.get("msg", "unexpected JSON response")))
            raise SaveFamilyResponseError(status, message, payload)
        raise SaveFamilyResponseError(None, "server returned JSON instead of a file", {"payload": payload})

    async def _request_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        accept: str = "application/json",
    ) -> tuple[bytes, str]:
        url = urljoin(f"{self.region.base_url}/", path.lstrip("/"))
        if self.ssl_context is None:
            self.ssl_context = await asyncio.get_running_loop().run_in_executor(
                None,
                create_ssl_context,
            )
        request_params = None
        request_data = None
        encrypted, index = encrypt_request(
            params if params is not None else data or {},
            form_encoded=data is not None,
        )
        if params is not None:
            request_params = {
                "encryptData": encrypted["encryptData"],
                "encryptIndex": encrypted["encryptIndex"],
            }
        else:
            request_data = json.dumps(encrypted, separators=(",", ":"))

        try:
            async with self.session.request(
                method,
                url,
                params=request_params,
                data=request_data,
                headers={
                    "Accept": accept,
                    "Content-Type": "application/json; charset=utf-8",
                    ENCRYPT_INDEX_HEADER: str(index),
                },
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                ssl=self.ssl_context,
            ) as response:
                raw = await response.read()
                content_type = response.content_type
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SaveFamilyConnectionError(f"request to {url} failed: {exc}") from exc

        if response.status in {401, 403}:
            raise SaveFamilyAuthError(f"HTTP {response.status}: {raw[:500].decode('utf-8', 'replace')}")
        if response.status >= 400:
            raise SaveFamilyConnectionError(f"HTTP {response.status}: {raw[:500].decode('utf-8', 'replace')}")
        return raw, content_type

    @staticmethod
    def _ensure_status(payload: dict[str, Any], allowed_statuses: set[int]) -> None:
        status = coerce_int(payload.get("status"))
        if status not in allowed_statuses:
            message = str(payload.get("message", "unknown server response"))
            raise SaveFamilyResponseError(status, message, payload)

    @staticmethod
    def _ensure_command_success(payload: dict[str, Any]) -> None:
        code = coerce_int(payload.get("code"))
        if code == 200:
            return
        status = coerce_int(payload.get("status"))
        if status in SUCCESS_STATUSES:
            return
        error_status = code if code is not None else status
        fallback = DEVICE_OFFLINE_MESSAGE if error_status == DEVICE_OFFLINE_STATUS else "unexpected command response"
        message = str(payload.get("message", payload.get("msg", fallback)))
        raise SaveFamilyResponseError(error_status, message, payload)

    @staticmethod
    def _ensure_login_success(payload: dict[str, Any]) -> None:
        status = coerce_int(payload.get("status"))
        if status in SUCCESS_STATUSES:
            return
        message = str(payload.get("message", "unknown login failure"))
        if status == 2:
            raise SaveFamilyAuthError(message)
        if status == 3:
            raise SaveFamilyUpgradeRequiredError(
                "SaveFamily server refused the login with a 'please upgrade the app' "
                "block (status 3). The server protocol has probably changed again; "
                f"check for an update of the integration. Server said: {message}"
            )
        raise SaveFamilyResponseError(status, message, payload)
