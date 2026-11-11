"""DataUpdateCoordinator for Everlights v3 Cloud."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    EverlightsCloudAuthenticationError,
    EverlightsCloudClient,
    EverlightsCloudCommandUnknownError,
    EverlightsCloudError,
)
from .const import (
    ACCOUNT_REFRESH_INTERVAL,
    DOMAIN,
    LOGGER,
    SEQUENCES_REFRESH_INTERVAL,
    UPDATE_INTERVAL,
    ZONE_STALE_AFTER,
)
from .patterns import normalize_sequence

type EverlightsCloudConfigEntry = ConfigEntry[EverlightsCloudCoordinator]

# Read-backs after a command: delays before each attempt, in seconds.
CONFIRM_DELAYS = (0.0, 1.0, 2.0)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse the bridge's ISO timestamps (UTC with a trailing Z)."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class EverlightsCloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll bridges and zones through the Everlights cloud.

    data = {
        "bridges": {serial: {alias, online, firmwareVersion, ...}},
        "zones":   {serial: {alias, bridgeSerial, online, stale, rssi, snr,
                             pattern, effects, ...}},
    }
    """

    config_entry: EverlightsCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EverlightsCloudConfigEntry,
        client: EverlightsCloudClient,
    ) -> None:
        """Initialize."""
        self.client = client
        self.sequences: list[dict[str, Any]] = []
        self._account: dict[str, dict[str, dict[str, Any]]] | None = None
        self._account_at: datetime | None = None
        self._sequences_at: datetime | None = None
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._async_poll()
        except EverlightsCloudAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EverlightsCloudError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_poll(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        if self._account is None or now - self._account_at > ACCOUNT_REFRESH_INTERVAL:  # type: ignore[operator]
            try:
                self._account = await self.client.async_get_account()
                self._account_at = now
            except EverlightsCloudAuthenticationError:
                raise
            except EverlightsCloudError:
                if self._account is None:
                    raise
                LOGGER.debug("Account refresh failed. Keeping the known zones")
        account = self._account
        if not account["bridges"]:
            raise UpdateFailed("No Everlights bridge is paired to this account")

        bridges: dict[str, dict[str, Any]] = {}
        zones: dict[str, dict[str, Any]] = {}
        previous = (self.data or {}).get("zones", {})
        errors: list[str] = []
        for serial, known in account["bridges"].items():
            bridge = dict(known, online=False)
            bridges[serial] = bridge
            my_zones = {z: i for z, i in account["zones"].items() if i["bridgeSerial"] == serial}
            try:
                status = await self.client.async_get_bridge_status(serial)
                live = {z.get("serial"): z for z in status["zones"]}
                for zone_serial, info in my_zones.items():
                    zone = dict(live.get(zone_serial) or {}, **info)
                    zone["online"] = zone_serial in live
                    if zone["online"]:
                        zone.update(
                            normalize_sequence(
                                await self.client.async_get_zone_sequence(serial, zone_serial)
                            )
                        )
                        seen = parse_timestamp(zone.get("lastResponseDate"))
                        zone["stale"] = seen is not None and now - seen > ZONE_STALE_AFTER
                    zones[zone_serial] = zone
                bridge.update({k: v for k, v in status.items() if k != "zones"})
                bridge["alias"] = known["alias"]
                bridge["online"] = True
                await self._async_maybe_load_sequences(serial, now)
            except EverlightsCloudAuthenticationError:
                raise
            except EverlightsCloudError as err:
                errors.append(f"{known['alias']}: {err}")
                for zone_serial, info in my_zones.items():
                    # Keep the last known readings but mark the zone offline.
                    zones[zone_serial] = dict(previous.get(zone_serial, {}), **info, online=False)
        if errors and not any(b["online"] for b in bridges.values()):
            raise UpdateFailed("; ".join(errors))
        return {"bridges": bridges, "zones": zones}

    async def _async_maybe_load_sequences(self, bridge: str, now: datetime) -> None:
        if self.sequences and now - self._sequences_at < SEQUENCES_REFRESH_INTERVAL:  # type: ignore[operator]
            return
        try:
            self.sequences = await self.client.async_get_sequences(bridge)
            self._sequences_at = now
        except EverlightsCloudAuthenticationError:
            raise
        except EverlightsCloudError as err:
            # Effects are optional. Plain colours still work without them.
            LOGGER.warning("Could not load the Everlights sequence library: %s", err)

    def zone(self, serial: str) -> dict[str, Any]:
        """Return the latest data for a zone."""
        return (self.data or {}).get("zones", {}).get(serial, {})

    def zone_available(self, serial: str) -> bool:
        """Return True when a zone can be read and controlled right now."""
        zone = self.zone(serial)
        return bool(zone.get("online")) and not zone.get("stale")

    async def async_send_sequence(self, serial: str, sequence: dict[str, Any]) -> None:
        """Send a lighting command and confirm it by reading the zone back.

        The command is sent once. If the read-back never shows it, this raises
        instead of pretending the lights changed.
        """
        zone = self.zone(serial)
        bridge = zone.get("bridgeSerial")
        if not bridge:
            raise HomeAssistantError("Unknown Everlights zone")
        wanted = normalize_sequence(sequence)
        outcome_unknown = False
        try:
            await self.client.async_set_zone_sequence(bridge, serial, wanted)
        except EverlightsCloudCommandUnknownError:
            outcome_unknown = True
            LOGGER.warning(
                "No answer to the command for %s. Checking what the lights are doing",
                zone.get("alias", serial),
            )
        except EverlightsCloudAuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Everlights rejected the login: {err}") from err
        except EverlightsCloudError as err:
            raise HomeAssistantError(f"Everlights command failed: {err}") from err

        actual: dict[str, list] | None = None
        for delay in CONFIRM_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            try:
                actual = normalize_sequence(
                    await self.client.async_get_zone_sequence(bridge, serial)
                )
            except EverlightsCloudError as err:
                LOGGER.debug("Read-back failed: %s", err)
                continue
            if actual == wanted:
                break
        if actual is not None:
            data = dict(self.data)
            data["zones"] = dict(data["zones"])
            data["zones"][serial] = dict(data["zones"][serial], **actual)
            self.async_set_updated_data(data)
        if actual != wanted:
            name = zone.get("alias", serial)
            if actual is None:
                raise HomeAssistantError(
                    f"Could not confirm the command for {name}. The lights may not have changed"
                )
            reason = "got no answer" if outcome_unknown else "was accepted"
            raise HomeAssistantError(
                f"The command for {name} {reason} but the lights did not change"
            )
