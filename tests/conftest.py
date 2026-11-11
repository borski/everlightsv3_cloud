"""Shared fixtures: a stateful fake of the Everlights cloud built from sanitized captures."""
from __future__ import annotations

import base64
import copy
import json
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.everlightsv3_cloud.const import CLOUD_URL, DOMAIN, REMOTE_URL

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"
# The moment the captures were taken, so "stale" maths lines up.
NOW = "2026-11-11T06:54:00+00:00"

USERNAME = "user@example.com"
PASSWORD = "correct-horse"
BRIDGE = "B0000000B001"
STRAY_BRIDGE = "B0000000FFFF"
GARAGE, FRONT, BACK, STRAY = "Z0000000A001", "Z0000000A002", "Z0000000A003", "Z0000000FFFF"
ALIASES = {GARAGE: "Garage", FRONT: "Front", BACK: "Back", STRAY: "Zone 1"}

# Each capture run numbered its placeholders independently. Map them back.
CLOUD_MAP = {"serial-01": BRIDGE, "serial-03": STRAY_BRIDGE, "serial-02": STRAY,
             "serial-04": GARAGE, "serial-05": FRONT, "serial-06": BACK}
PROXY_MAP = {"serial-05": BRIDGE, "serial-02": GARAGE, "serial-03": FRONT, "serial-04": BACK}


def load_fixture(name: str, mapping: dict[str, str] | None = None) -> Any:
    """Load a sanitized capture, swapping placeholder serials for fake ones."""
    text = (FIXTURES / f"{name}.json").read_text()
    for placeholder, serial in (mapping or {}).items():
        text = text.replace(f'"{placeholder}"', f'"{serial}"')
    return json.loads(text)


def make_jwt(lifetime: int = 86400) -> str:
    """An unsigned token with a real exp claim."""
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + lifetime})}.sig"


class FakeCloud:
    """The app server plus the remote tunnel, with knobs for failure modes."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        self.password = PASSWORD
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.remote_tokens: set[str] = set()
        self.bridges = load_fixture("bridges", CLOUD_MAP)
        self.zones = load_fixture("zones", CLOUD_MAP)
        for item in self.zones:
            item["alias"] = ALIASES[item["zoneSerial"]]
        self.bridges[0]["alias"] = "EverLights Bridge"
        self.status = load_fixture("proxy_root", PROXY_MAP)
        self.library = load_fixture("proxy_sequences")
        for index, sequence in enumerate(self.library):
            sequence["alias"] = f"Sequence {index:03d}"
        self.sequences: dict[str, dict] = {
            z: {"pattern": [], "effects": []} for z in (GARAGE, FRONT, BACK)
        }
        self.calls: list[tuple[str, str]] = []
        self.posts: list[tuple[str, dict]] = []
        # Knobs. Each maps a call label (see _label) to a queue of overrides.
        self.fail: dict[str, list[Any]] = {}
        self.ignore_writes = False
        self.expires_in = 86400

        mocker.post(f"{CLOUD_URL}/oauth/token", side_effect=self._token)
        mocker.get(re.compile(rf"{re.escape(CLOUD_URL)}/.*"), side_effect=self._cloud)
        mocker.get(re.compile(rf"{re.escape(REMOTE_URL)}/.*"), side_effect=self._remote)
        mocker.post(re.compile(rf"{re.escape(REMOTE_URL)}/.*"), side_effect=self._remote)

    # -- helpers
    def count(self, method: str, label: str) -> int:
        return sum(1 for call in self.calls if call == (method, label))

    def queue(self, label: str, *overrides: Any) -> None:
        """Make the next calls to `label` answer with a status, or raise."""
        self.fail.setdefault(label, []).extend(overrides)

    def _override(self, method, url, label):
        queue = self.fail.get(label)
        if not queue:
            return None
        item = queue.pop(0)
        if item is None:
            return None
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        status, body = item if isinstance(item, tuple) else (item, "")
        headers = {"Retry-After": "30"} if status == 429 else {}
        if isinstance(body, str):
            return AiohttpClientMockResponse(method, url, status=status, text=body, headers=headers)
        return AiohttpClientMockResponse(method, url, status=status, json=body, headers=headers)

    def _json(self, method, url, body, status=200):
        return AiohttpClientMockResponse(method, url, status=status, json=copy.deepcopy(body))

    @staticmethod
    def _bearer(headers) -> str:
        return (headers or {}).get("Authorization", "").removeprefix("Bearer ")

    # -- app server
    async def _token(self, method, url, data):
        self.calls.append((method, f"token:{data.get('grant_type')}"))
        if (response := self._override(method, url, "token")) is not None:
            return response
        if data.get("grant_type") == "password":
            ok = data.get("username") == USERNAME and data.get("password") == self.password
        else:
            ok = data.get("refresh_token") in self.refresh_tokens
            self.refresh_tokens.discard(data.get("refresh_token"))
        if not ok:
            return self._json(method, url, {"error": "invalid_grant"}, status=400)
        access, refresh = make_jwt(), f"refresh-{len(self.calls)}"
        self.access_tokens.add(access)
        self.refresh_tokens.add(refresh)
        return self._json(method, url, {"token_type": "Bearer", "access_token": access,
                                        "refresh_token": refresh, "expires_in": self.expires_in})

    async def _cloud(self, method, url, data):
        path = url.path.removeprefix("/v1/")
        label = "remotetoken" if path.endswith("/remotetoken") else path
        self.calls.append((method, label))
        if (response := self._override(method, url, label)) is not None:
            return response
        if self._bearer(self.mocker.mock_calls[-1][3]) not in self.access_tokens:
            return AiohttpClientMockResponse(
                method, url, status=400,
                text='Authorization failure: {"name":"JsonWebTokenError","message":"invalid token"}')
        if path == "bridges":
            return self._json(method, url, self.bridges)
        if path == "zones":
            return self._json(method, url, self.zones)
        if label == "remotetoken":
            token = make_jwt()
            self.remote_tokens.add(token)
            return self._json(method, url, {"token": token})
        return self._json(method, url, {"error": "not found"}, status=404)

    # -- tunnel
    async def _remote(self, method, url, data):
        match = re.match(r"^/bridges/([^/]+)/(.*)$", url.path)
        bridge, path = match.group(1), match.group(2).removeprefix("proxy/v1/")
        zone = None
        if seq := re.match(r"^zones/([^/]+)/sequence$", path):
            zone, label = seq.group(1), "sequence"
        else:
            label = path or "status"
        self.calls.append((method, label))
        if method == "POST":
            self.posts.append((zone, copy.deepcopy(data)))
        if (response := self._override(method, url, label)) is not None:
            return response
        if self._bearer(self.mocker.mock_calls[-1][3]) not in self.remote_tokens or bridge != BRIDGE:
            return AiohttpClientMockResponse(method, url, status=401, text="")
        if label == "status":
            return self._json(method, url, self.status)
        if label == "sequences":
            return self._json(method, url, self.library)
        if label == "connection":
            return self._json(method, url, load_fixture("remote_connection", PROXY_MAP))
        if label == "sequence" and method == "GET":
            return self._json(method, url, self.sequences[zone])
        if label == "sequence" and method == "POST":
            if not self.ignore_writes:
                self.sequences[zone] = copy.deepcopy(data)
            return self._json(method, url, self.sequences[zone])
        return self._json(method, url, {"error": "not found"}, status=404)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration under test."""


@pytest.fixture(autouse=True)
def frozen_now(freezer):
    """Pin the clock to when the captures were taken."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture(autouse=True)
def no_sleep():
    """Skip retry and confirm delays."""
    with (
        patch("custom_components.everlightsv3_cloud.api.RETRY_DELAY", 0),
        patch("custom_components.everlightsv3_cloud.api.WRITE_GAP", 0),
        patch("custom_components.everlightsv3_cloud.coordinator.CONFIRM_DELAYS", (0, 0, 0)),
    ):
        yield


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> FakeCloud:
    """The fake Everlights cloud."""
    return FakeCloud(aioclient_mock)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry for the fake account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )


@pytest.fixture
async def setup_entry(hass, cloud, mock_config_entry) -> MockConfigEntry:
    """Set the integration up against the fake cloud."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def find_device(registry, identifier: tuple[str, str], entry_id: str):
    """Look a device up by identifier on old and new Home Assistant versions."""
    lookup = getattr(registry, "async_get_device_by_identifier", None)
    if lookup is not None:
        return lookup(identifier, entry_id)
    return registry.async_get_device(identifiers={identifier})
