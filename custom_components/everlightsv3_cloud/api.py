"""Async client for the Everlights cloud and its remote bridge tunnel.

Bridge firmware B5010 removed the local control API. The official app now
talks to two hosts, and so does this client:

* appserver-v2.myeverlights.com: account login, bridges, zones, and minting
  a per-bridge "remote token".
* remote.myeverlights.com: a tunnel to the bridge. Under
  /bridges/{serial}/proxy/v1/ it exposes the same API the bridge used to
  serve on the LAN.

Rules this client follows:

* Reads (GET) are retried on transient failures.
* Writes (POST, PUT, DELETE) are sent exactly once. A timed-out write is reported as
  "outcome unknown" and never replayed. Callers confirm by reading back.
* An auth rejection is a definitive "not executed", so one re-authenticated
  attempt is allowed for any verb.
"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import time
from typing import Any

import aiohttp

from .const import CLOUD_URL, LOGGER, REMOTE_URL

REQUEST_TIMEOUT = 15
READ_ATTEMPTS = 3
# Refresh tokens this long before they expire.
TOKEN_MARGIN = 300
# Minimum gap between writes. The bridge misbehaves when writes are stacked.
WRITE_GAP = 0.25
# Base delay between read retries, multiplied by the attempt number.
RETRY_DELAY = 0.5

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "HomeAssistant-everlightsv3_cloud/0.2",
}


class EverlightsCloudError(Exception):
    """Base error."""


class EverlightsCloudCommunicationError(EverlightsCloudError):
    """The cloud or the bridge could not be reached, or answered 5xx."""


class EverlightsCloudRateLimitError(EverlightsCloudCommunicationError):
    """HTTP 429."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Keep the server's Retry-After hint, if it sent one."""
        super().__init__(message)
        self.retry_after = retry_after


class EverlightsCloudAuthenticationError(EverlightsCloudError):
    """Credentials were rejected."""


class EverlightsCloudCommandUnknownError(EverlightsCloudError):
    """A write was sent but no answer came back. It may or may not have applied."""


def _jwt_exp(token: str) -> float | None:
    """Read the exp claim of a JWT without verifying it."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _retry_after(headers: Any) -> float | None:
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


class EverlightsCloudClient:
    """Everlights cloud client. One instance per account."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        """Initialize."""
        self._session = session
        self._username = username
        self._password = password
        self._access: str | None = None
        self._access_exp = 0.0
        self._refresh: str | None = None
        self._remote: dict[str, tuple[str, float]] = {}
        self._auth_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0

    # ------------------------------------------------------------------ auth

    async def async_login(self) -> None:
        """Log in with the account password, replacing any tokens held."""
        async with self._auth_lock:
            await self._async_password_grant()

    async def _async_token_request(self, payload: dict[str, str]) -> None:
        try:
            status, body, headers = await self._async_send(
                "POST", f"{CLOUD_URL}/oauth/token", json_body=payload
            )
        except EverlightsCloudCommandUnknownError as err:
            # Not a lighting command. A lost login is just a connection problem.
            raise EverlightsCloudCommunicationError(str(err)) from err
        if status in (400, 401, 403):
            # Verified: a bad refresh token answers 400 invalid_grant.
            raise EverlightsCloudAuthenticationError("Everlights rejected the credentials")
        self._raise_for_status(status, headers, "oauth/token")
        if not isinstance(body, dict) or not body.get("access_token"):
            raise EverlightsCloudError("Unexpected token response")
        self._access = body["access_token"]
        self._refresh = body.get("refresh_token") or self._refresh
        expires_in = body.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self._access_exp = time.time() + expires_in
        else:
            self._access_exp = _jwt_exp(self._access) or time.time() + 3600

    async def _async_password_grant(self) -> None:
        await self._async_token_request(
            {"grant_type": "password", "username": self._username, "password": self._password}
        )

    async def _async_ensure_access(self, force: bool = False) -> str:
        async with self._auth_lock:
            if force:
                self._access = None
            if self._access and time.time() < self._access_exp - TOKEN_MARGIN:
                return self._access
            if self._refresh and not force:
                try:
                    # Verified: the refresh token rotates on every use.
                    await self._async_token_request(
                        {"grant_type": "refresh_token", "refresh_token": self._refresh}
                    )
                    return self._access  # type: ignore[return-value]
                except EverlightsCloudAuthenticationError:
                    LOGGER.debug("Refresh token rejected. Logging in again")
                    self._refresh = None
            await self._async_password_grant()
            return self._access  # type: ignore[return-value]

    async def _async_ensure_remote(self, bridge: str, force: bool = False) -> str:
        cached = self._remote.get(bridge)
        if cached and not force and time.time() < cached[1] - TOKEN_MARGIN:
            return cached[0]
        body = await self._async_cloud_get(f"bridges/{bridge}/remotetoken")
        token = body.get("token") if isinstance(body, dict) else None
        if not token:
            raise EverlightsCloudError("Unexpected remote token response")
        self._remote[bridge] = (token, _jwt_exp(token) or time.time() + 3600)
        return token

    # ------------------------------------------------------------- transport

    @staticmethod
    def _raise_for_status(status: int, headers: Any, what: str) -> None:
        if status == 429:
            raise EverlightsCloudRateLimitError(
                f"Rate limited on {what}", retry_after=_retry_after(headers)
            )
        if status >= 500:
            raise EverlightsCloudCommunicationError(f"HTTP {status} on {what}")
        if status >= 400:
            raise EverlightsCloudError(f"HTTP {status} on {what}")

    async def _async_send(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        json_body: Any = None,
    ) -> tuple[int, Any, Any]:
        """Send one request. Never retries. Never logs tokens or bodies."""
        headers = dict(HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, url, headers=headers, json=json_body
                ) as response:
                    text = await response.text()
                    body: Any = text
                    if text:
                        try:
                            body = json.loads(text)
                        except ValueError:
                            body = text
                    return response.status, body, response.headers
        except TimeoutError as err:
            if method != "GET":
                raise EverlightsCloudCommandUnknownError(
                    f"No answer to {method}. The command may or may not have applied"
                ) from err
            raise EverlightsCloudCommunicationError(f"Timeout on {method}") from err
        except (aiohttp.ClientError, socket.gaierror) as err:
            if method != "GET" and not isinstance(err, aiohttp.ClientConnectorError):
                # The connection dropped after the request may have been sent.
                raise EverlightsCloudCommandUnknownError(
                    f"Connection lost during {method}: {type(err).__name__}"
                ) from err
            raise EverlightsCloudCommunicationError(
                f"Connection error on {method}: {type(err).__name__}"
            ) from err

    async def _async_read(self, send) -> tuple[int, Any, Any]:
        """Run an idempotent request with retries on transient failures."""
        last: EverlightsCloudCommunicationError | None = None
        for attempt in range(1, READ_ATTEMPTS + 1):
            try:
                status, body, headers = await send()
                if status >= 500 and attempt < READ_ATTEMPTS:
                    last = EverlightsCloudCommunicationError(f"HTTP {status}")
                else:
                    return status, body, headers
            except EverlightsCloudRateLimitError:
                raise
            except EverlightsCloudCommunicationError as err:
                last = err
                if attempt == READ_ATTEMPTS:
                    break
            await asyncio.sleep(RETRY_DELAY * attempt)
        raise last or EverlightsCloudCommunicationError("Request failed")

    @staticmethod
    def _cloud_auth_rejected(status: int, body: Any) -> bool:
        # Verified: the app server answers 400 "Authorization failure: ..." to
        # a bad bearer token, not 401.
        if status in (401, 403):
            return True
        return status == 400 and isinstance(body, str) and "Authorization failure" in body

    async def _async_cloud_get(self, path: str) -> Any:
        url = f"{CLOUD_URL}/{path}"
        for force in (False, True):
            token = await self._async_ensure_access(force=force)
            status, body, headers = await self._async_read(
                lambda token=token: self._async_send("GET", url, token=token)
            )
            if not self._cloud_auth_rejected(status, body):
                self._raise_for_status(status, headers, path.split("/")[0])
                return body
        raise EverlightsCloudAuthenticationError("Everlights rejected a fresh access token")

    async def _async_remote(
        self, method: str, bridge: str, path: str, json_body: Any = None, once: bool = False
    ) -> Any:
        url = f"{REMOTE_URL}/{bridge}/{path}"
        what = f"bridge {method} {path.split('/')[0]}"
        for force in (False, True):
            token = await self._async_ensure_remote(bridge, force=force)
            if method == "GET" and not once:
                status, body, headers = await self._async_read(
                    lambda token=token: self._async_send("GET", url, token=token)
                )
            else:
                status, body, headers = await self._async_send(
                    method, url, token=token, json_body=json_body
                )
            if status == 401:
                # Rejected before reaching the bridge, so a second go is safe.
                continue
            self._raise_for_status(status, headers, what)
            return body
        raise EverlightsCloudAuthenticationError("Everlights rejected a fresh bridge token")

    # ------------------------------------------------------------------ reads

    async def async_get_account(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return the account's bridges and the zones attached to them.

        Zones paired to the account but attached to a bridge the account does
        not own cannot be reached through the tunnel, so they are left out.
        """
        bridges_raw = await self._async_cloud_get("bridges")
        zones_raw = await self._async_cloud_get("zones")
        if not isinstance(bridges_raw, list) or not isinstance(zones_raw, list):
            raise EverlightsCloudError("Unexpected account payload")
        bridges: dict[str, dict[str, Any]] = {}
        for item in bridges_raw:
            serial = item.get("bridgeSerial")
            if not serial or item.get("unpairedDate"):
                continue
            info = item.get("bridge") or {}
            bridges[serial] = {
                "serial": serial,
                "alias": item.get("alias") or "EverLights Bridge",
                "firmwareVersion": info.get("firmwareVersion"),
                "hardwareVersion": info.get("hardwareVersion"),
            }
        zones: dict[str, dict[str, Any]] = {}
        for item in zones_raw:
            serial = item.get("zoneSerial")
            info = item.get("zone") or {}
            bridge = info.get("bridgeSerial")
            if not serial or item.get("unpairedDate") or bridge not in bridges:
                continue
            zones[serial] = {
                "serial": serial,
                "alias": item.get("alias") or serial,
                "bridgeSerial": bridge,
                "model": info.get("hardwareVersion"),
            }
        return {"bridges": bridges, "zones": zones}

    async def async_get_bridge_status(self, bridge: str) -> dict[str, Any]:
        """Return the bridge status document, including its zones."""
        body = await self._async_remote("GET", bridge, "proxy/v1/")
        if not isinstance(body, dict) or not isinstance(body.get("zones"), list):
            raise EverlightsCloudError("Unexpected bridge status payload")
        return body

    async def async_get_connection(self, bridge: str) -> dict[str, Any]:
        """Return the tunnel's view of the bridge connection."""
        body = await self._async_remote("GET", bridge, "connection")
        return body if isinstance(body, dict) else {}

    async def async_get_sequences(self, bridge: str) -> list[dict[str, Any]]:
        """Return the sequence library stored on the bridge."""
        body = await self._async_remote("GET", bridge, "proxy/v1/sequences")
        if not isinstance(body, list):
            raise EverlightsCloudError("Unexpected sequences payload")
        return [s for s in body if isinstance(s, dict)]

    async def async_get_zone_sequence(self, bridge: str, zone: str) -> dict[str, Any]:
        """Return what a zone is showing: {"pattern": [...], "effects": [...]}."""
        body = await self._async_remote("GET", bridge, f"proxy/v1/zones/{zone}/sequence")
        if not isinstance(body, dict):
            raise EverlightsCloudError("Unexpected zone sequence payload")
        return body

    async def async_get_sequence(self, bridge: str, sequence_id: str) -> dict[str, Any]:
        """Return one saved sequence by id."""
        body = await self._async_remote("GET", bridge, f"proxy/v1/sequences/{sequence_id}")
        if not isinstance(body, dict):
            raise EverlightsCloudError("Unexpected sequence payload")
        return body

    async def async_get_events(self, bridge: str) -> list[dict[str, Any]]:
        """Return the shows scheduled on the bridge."""
        body = await self._async_remote("GET", bridge, "proxy/v1/events")
        if not isinstance(body, list):
            raise EverlightsCloudError("Unexpected events payload")
        return [e for e in body if isinstance(e, dict)]

    async def async_get_time(self, bridge: str) -> str | None:
        """Return the bridge's clock as an ISO timestamp."""
        body = await self._async_remote("GET", bridge, "proxy/v1/time")
        return body.get("time") if isinstance(body, dict) else None

    async def async_get_profile(self) -> dict[str, Any]:
        """Return the account record (id, email, names, dates)."""
        body = await self._async_cloud_get("account")
        return body if isinstance(body, dict) else {}

    async def async_get_shared_sequences(self) -> list[dict[str, Any]]:
        """Return sequences other accounts have shared with this one."""
        body = await self._async_cloud_get("shared/sequences")
        return [s for s in body if isinstance(s, dict)] if isinstance(body, list) else []

    async def async_get_min_app_version(self) -> str | None:
        """Return the oldest app version the cloud still accepts."""
        body = await self._async_cloud_get("minappversion")
        return body.strip() if isinstance(body, str) and body.strip() else None

    # ----------------------------------------------------------------- writes

    async def _async_write(self, method: str, bridge: str, path: str, json_body: Any = None) -> Any:
        """Send one write through the tunnel. Sent once. Never replayed.

        A GET passed here is a command in disguise (reboot) and gets the same treatment.
        """
        async with self._write_lock:
            wait = WRITE_GAP - (time.monotonic() - self._last_write)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._async_remote(
                    method, bridge, path, json_body=json_body, once=True
                )
            except EverlightsCloudCommunicationError as err:
                if method == "GET" and "Timeout" in str(err):
                    raise EverlightsCloudCommandUnknownError(
                        "No answer. The command may or may not have applied"
                    ) from err
                raise
            finally:
                self._last_write = time.monotonic()

    async def async_set_zone_sequence(
        self, bridge: str, zone: str, sequence: dict[str, Any]
    ) -> Any:
        """Send one lighting command. Sent once. Never replayed.

        Raises EverlightsCloudCommandUnknownError when no answer came back.
        A normal return only means the cloud accepted the request. Read the
        zone back to learn whether the lights changed.
        """
        return await self._async_write(
            "POST", bridge, f"proxy/v1/zones/{zone}/sequence", json_body=sequence
        )

    async def async_clear_zone_sequence(self, bridge: str, zone: str) -> None:
        """Drop whatever was sent to a zone and hand it back to the bridge's schedule.

        Verified on B5010: answers 204, and with nothing scheduled the zone goes dark.
        """
        await self._async_write("DELETE", bridge, f"proxy/v1/zones/{zone}/sequence")

    async def async_create_sequence(self, bridge: str, sequence: dict[str, Any]) -> Any:
        """Save a sequence in the bridge's library. Verified on B5010: answers 201."""
        return await self._async_write("POST", bridge, "proxy/v1/sequences", json_body=sequence)

    async def async_delete_sequence(self, bridge: str, sequence_id: str) -> None:
        """Delete a saved sequence by id. Verified on B5010: answers 204."""
        await self._async_write("DELETE", bridge, f"proxy/v1/sequences/{sequence_id}")

    async def async_update_sequence(
        self, bridge: str, sequence_id: str, sequence: dict[str, Any]
    ) -> Any:
        """Replace a saved sequence. The old local API answered 200."""
        return await self._async_write(
            "PUT", bridge, f"proxy/v1/sequences/{sequence_id}", json_body=sequence
        )

    async def async_create_event(self, bridge: str, event: dict[str, Any]) -> Any:
        """Schedule a show. The old local API answered 201."""
        return await self._async_write("POST", bridge, "proxy/v1/events", json_body=event)

    async def async_update_event(self, bridge: str, event_id: str, event: dict[str, Any]) -> Any:
        """Replace a scheduled show. The old local API answered 200."""
        return await self._async_write(
            "PUT", bridge, f"proxy/v1/events/{event_id}", json_body=event
        )

    async def async_delete_event(self, bridge: str, event_id: str) -> None:
        """Remove a scheduled show. The old local API answered 204."""
        await self._async_write("DELETE", bridge, f"proxy/v1/events/{event_id}")

    async def async_set_time(self, bridge: str, when: str) -> Any:
        """Set the bridge's clock to an ISO timestamp."""
        return await self._async_write("PUT", bridge, "proxy/v1/time", json_body={"time": when})

    async def async_reboot(self, bridge: str) -> None:
        """Restart the bridge. It is a GET on the bridge, but it is sent once like a write."""
        await self._async_write("GET", bridge, "proxy/v1/reboot")
