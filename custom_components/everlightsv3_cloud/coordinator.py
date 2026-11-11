"""DataUpdateCoordinator for Everlights v3 Cloud."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
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
    EVENTS_REFRESH_INTERVAL,
    LOGGER,
    PERSONAL_GROUP,
    SEQUENCES_REFRESH_INTERVAL,
    UPDATE_INTERVAL,
    ZONE_STALE_AFTER,
)
from .patterns import normalize_effects, normalize_pattern, normalize_sequence
from .schedule import (
    ALL_ZONES,
    DEFAULT_DURATION,
    build_event,
    flags_with,
    nightly_occurrences,
    parse_stamp,
    split_uid,
    stamp,
)

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
        # Scheduled shows per bridge serial.
        self.events: dict[str, list[dict[str, Any]]] = {}
        self._events_at: dict[str, datetime] = {}
        self._clock_offset: dict[str, float | None] = {}
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
                await self._async_maybe_load_events(serial, now)
                bridge["clockOffset"] = self._clock_offset.get(serial)
                bridge["eventCount"] = len(self.events.get(serial, []))
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

    async def _async_maybe_load_events(self, bridge: str, now: datetime) -> None:
        """Load the schedule and the bridge clock. Both are optional extras."""
        last = self._events_at.get(bridge)
        if last is not None and now - last < EVENTS_REFRESH_INTERVAL:
            return
        self._events_at[bridge] = now
        try:
            self.events[bridge] = await self.client.async_get_events(bridge)
            bridge_time = parse_stamp(await self.client.async_get_time(bridge))
            self._clock_offset[bridge] = (
                round((bridge_time - dt_util.utcnow()).total_seconds(), 1) if bridge_time else None
            )
        except EverlightsCloudAuthenticationError:
            raise
        except EverlightsCloudError as err:
            LOGGER.debug("Could not load the Everlights schedule: %s", err)

    def bridge(self, serial: str) -> dict[str, Any]:
        """Return the latest data for a bridge."""
        return (self.data or {}).get("bridges", {}).get(serial, {})

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

    # ------------------------------------------------------------ more writes

    def _bridge_of(self, serial: str) -> str:
        bridge = self.zone(serial).get("bridgeSerial")
        if not bridge:
            raise HomeAssistantError("Unknown Everlights zone")
        return bridge

    async def _async_write(self, what: str, call) -> bool:
        """Run one write. Return False when no answer came back, so the caller checks."""
        try:
            await call
        except EverlightsCloudCommandUnknownError:
            LOGGER.warning("No answer to %s. Checking what the bridge did", what)
            return False
        except EverlightsCloudAuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Everlights rejected the login: {err}") from err
        except EverlightsCloudError as err:
            raise HomeAssistantError(f"Everlights could not {what}: {err}") from err
        return True

    async def _async_reload_sequences(self, bridge: str) -> None:
        try:
            self.sequences = await self.client.async_get_sequences(bridge)
            self._sequences_at = dt_util.utcnow()
        except EverlightsCloudError as err:
            raise HomeAssistantError(
                f"Could not read the sequence library back, so the result is unknown: {err}"
            ) from err
        self.async_update_listeners()

    async def async_clear_zone(self, serial: str) -> None:
        """Drop the override on a zone and show whatever the bridge falls back to."""
        bridge = self._bridge_of(serial)
        name = self.zone(serial).get("alias", serial)
        answered = await self._async_write(
            f"clear {name}", self.client.async_clear_zone_sequence(bridge, serial)
        )
        try:
            actual = normalize_sequence(await self.client.async_get_zone_sequence(bridge, serial))
        except EverlightsCloudError as err:
            raise HomeAssistantError(
                f"Could not read {name} back after clearing it: {err}"
            ) from err
        data = dict(self.data)
        data["zones"] = dict(data["zones"])
        data["zones"][serial] = dict(data["zones"][serial], **actual)
        self.async_set_updated_data(data)
        if not answered:
            raise HomeAssistantError(
                f"The clear command for {name} got no answer. The state shown is what the bridge reports now"
            )

    async def async_save_sequence(
        self, serial: str, name: str, pattern: list[str], effects: list[dict[str, Any]]
    ) -> None:
        """Save a new sequence in the bridge's library and confirm it is listed."""
        bridge = self._bridge_of(serial)
        name = name.strip()
        pattern, effects = normalize_pattern(pattern), normalize_effects(effects)
        if not name or not pattern:
            raise ServiceValidationError("A sequence needs a name and at least one colour")
        await self._async_reload_sequences(bridge)
        if any(s.get("alias", "").casefold() == name.casefold() for s in self.sequences):
            raise ServiceValidationError(f"A sequence named '{name}' already exists")
        sequence_id = str(uuid.uuid4())
        await self._async_write(
            f"save sequence '{name}'",
            self.client.async_create_sequence(
                bridge,
                {
                    "id": sequence_id,
                    "alias": name,
                    "groups": [PERSONAL_GROUP],
                    "colorMode": "rgb",
                    "pattern": pattern,
                    "effects": effects,
                    "lastChanged": dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                },
            ),
        )
        await self._async_reload_sequences(bridge)
        if not any(s.get("id") == sequence_id for s in self.sequences):
            raise HomeAssistantError(f"The bridge did not save the sequence '{name}'")

    async def async_delete_sequence(self, serial: str, name: str) -> None:
        """Delete one of your own saved sequences and confirm it is gone."""
        bridge = self._bridge_of(serial)
        await self._async_reload_sequences(bridge)
        sequence_id = self._own_sequence(name)["id"]
        await self._async_write(
            f"delete sequence '{name}'", self.client.async_delete_sequence(bridge, sequence_id)
        )
        await self._async_reload_sequences(bridge)
        if any(s.get("id") == sequence_id for s in self.sequences):
            raise HomeAssistantError(f"The bridge did not delete the sequence '{name}'")

    async def async_update_sequence(
        self,
        serial: str,
        name: str,
        pattern: list[str] | None,
        effects: list[dict[str, Any]] | None,
        new_name: str | None = None,
    ) -> None:
        """Change one of your own saved sequences and confirm the bridge kept it."""
        bridge = self._bridge_of(serial)
        await self._async_reload_sequences(bridge)
        current = self._own_sequence(name)
        changed = dict(current)
        if pattern is not None:
            changed["pattern"] = normalize_pattern(pattern)
            if not changed["pattern"]:
                raise ServiceValidationError("A sequence needs at least one colour")
        if effects is not None:
            changed["effects"] = normalize_effects(effects)
        if new_name and new_name.strip().casefold() != name.strip().casefold():
            new_name = new_name.strip()
            if any(s.get("alias", "").casefold() == new_name.casefold() for s in self.sequences):
                raise ServiceValidationError(f"A sequence named '{new_name}' already exists")
            changed["alias"] = new_name
        changed["lastChanged"] = stamp(dt_util.utcnow())
        await self._async_write(
            f"update sequence '{name}'",
            self.client.async_update_sequence(bridge, current["id"], changed),
        )
        await self._async_reload_sequences(bridge)
        stored = next((s for s in self.sequences if s.get("id") == current["id"]), None)
        if (
            stored is None
            or normalize_sequence(stored) != normalize_sequence(changed)
            or stored.get("alias") != changed.get("alias")
        ):
            raise HomeAssistantError(f"The bridge did not keep the change to '{name}'")

    def _own_sequence(self, name: str) -> dict[str, Any]:
        named = [s for s in self.sequences if s.get("alias", "").casefold() == name.strip().casefold()]
        if not named:
            raise ServiceValidationError(f"No sequence named '{name}'")
        own = [s for s in named if PERSONAL_GROUP in (s.get("groups") or []) and s.get("id")]
        if not own:
            raise ServiceValidationError(f"'{name}' is a built-in sequence and can't be changed")
        if len(own) > 1:
            raise ServiceValidationError(
                f"More than one of your sequences is named '{name}'. Change it in the app"
            )
        return own[0]

    # ---------------------------------------------------------------- schedule

    async def _async_reload_events(self, bridge: str) -> None:
        try:
            self.events[bridge] = await self.client.async_get_events(bridge)
            self._events_at[bridge] = dt_util.utcnow()
        except EverlightsCloudError as err:
            raise HomeAssistantError(
                f"Could not read the schedule back, so the result is unknown: {err}"
            ) from err
        data = dict(self.data)
        data["bridges"] = dict(data["bridges"])
        data["bridges"][bridge] = dict(data["bridges"][bridge], eventCount=len(self.events[bridge]))
        self.async_set_updated_data(data)

    def _sequence_id(self, name: str) -> str:
        """Resolve a sequence name to its id.

        Where a name is duplicated the later copy wins, the same one the effect list uses.
        """
        named = [
            s for s in self.sequences
            if s.get("alias", "").casefold() == name.strip().casefold() and s.get("id")
        ]
        if not named:
            raise ServiceValidationError(f"No sequence named '{name}'")
        return named[-1]["id"]

    async def async_create_event(
        self,
        zone_serials: list[str],
        name: str,
        sequences: list[tuple[str, int | None]],
        start: datetime,
        end: datetime,
        *,
        nights: int = 1,
        start_at_dusk: bool = False,
        end_at_dawn: bool = False,
    ) -> str:
        """Schedule a show on the bridge that owns the zones. Returns the event id."""
        name = name.strip()
        bridges = {self._bridge_of(z) for z in zone_serials}
        if not name or not sequences or len(bridges) != 1:
            raise ServiceValidationError(
                "A show needs a name, at least one sequence, and zones on a single bridge"
            )
        bridge = bridges.pop()
        if not self.sequences:
            await self._async_reload_sequences(bridge)
        resolved = [(self._sequence_id(n), secs or DEFAULT_DURATION) for n, secs in sequences]
        try:
            occurrences = nightly_occurrences(start, end, nights)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        serials = self._zone_field(bridge, zone_serials)
        event = build_event(
            name, resolved, serials, occurrences,
            start_at_dusk=start_at_dusk, end_at_dawn=end_at_dawn, now=dt_util.utcnow(),
        )
        await self._async_write(
            f"schedule '{name}'", self.client.async_create_event(bridge, event)
        )
        await self._async_reload_events(bridge)
        if not any(e.get("id") == event["id"] for e in self.events[bridge]):
            raise HomeAssistantError(f"The bridge did not schedule '{name}'")
        return event["id"]

    def _zone_field(self, bridge: str, zone_serials: list[str]) -> list[str]:
        """The zoneSerials value for a show: the app's all-zones marker when it covers every zone."""
        every = {z for z, info in self.data["zones"].items() if info.get("bridgeSerial") == bridge}
        return [ALL_ZONES] if set(zone_serials) == every else sorted(set(zone_serials))

    def _show_named(self, bridge: str, name: str) -> dict[str, Any]:
        named = [
            e for e in self.events[bridge]
            if str(e.get("alias", "")).casefold() == name.strip().casefold()
        ]
        if not named:
            raise ServiceValidationError(f"No show named '{name}'")
        if len(named) > 1:
            raise ServiceValidationError(f"More than one show is named '{name}'")
        return named[0]

    async def async_update_event(
        self,
        bridge: str,
        name: str,
        *,
        new_name: str | None = None,
        sequences: list[tuple[str, int | None]] | None = None,
        zone_serials: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        nights: int | None = None,
        start_at_dusk: bool | None = None,
        end_at_dawn: bool | None = None,
    ) -> None:
        """Change a show in place. Anything left out stays as it is."""
        await self._async_reload_events(bridge)
        event = self._show_named(bridge, name)
        changed = dict(event)
        if new_name and new_name.strip():
            changed["alias"] = new_name.strip()
        if sequences is not None:
            if not self.sequences:
                await self._async_reload_sequences(bridge)
            changed["sequences"] = [
                {"sequenceId": self._sequence_id(n), "duration": int(secs or DEFAULT_DURATION)}
                for n, secs in sequences
            ]
        if zone_serials is not None:
            changed["zoneSerials"] = self._zone_field(bridge, zone_serials)
        if (start is None) != (end is None):
            raise ServiceValidationError("Pass both a start and an end to move a show")
        if start is not None and end is not None:
            try:
                changed["occurrences"] = nightly_occurrences(
                    start, end, nights or len(event.get("occurrences") or []) or 1
                )
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
        elif nights is not None:
            raise ServiceValidationError("Pass a start and an end along with the number of nights")
        changed["flags"] = flags_with(list(event.get("flags") or []), start_at_dusk, end_at_dawn)
        keys = ("alias", "sequences", "zoneSerials", "occurrences", "flags")
        if all(changed.get(k) == event.get(k) for k in keys):
            raise ServiceValidationError("Nothing to change")
        changed["lastChanged"] = stamp(dt_util.utcnow())
        event_id = str(event["id"])
        await self._async_write(
            f"change '{name}'", self.client.async_update_event(bridge, event_id, changed)
        )
        await self._async_reload_events(bridge)
        stored = next((e for e in self.events[bridge] if str(e.get("id")) == event_id), None)
        if stored is None or any(stored.get(k) != changed.get(k) for k in keys):
            raise HomeAssistantError(f"The bridge did not keep the change to '{name}'")

    async def async_delete_event(self, bridge: str, uid: str) -> None:
        """Remove a show, or one night of it when the uid names a night."""
        await self._async_reload_events(bridge)
        event_id, index = split_uid(uid)
        event = next((e for e in self.events[bridge] if str(e.get("id")) == event_id), None)
        if event is None:
            event_id, index = uid, None
            event = next((e for e in self.events[bridge] if str(e.get("id")) == uid), None)
        if event is None:
            raise ServiceValidationError("That show is no longer on the bridge")
        name = event.get("alias", event_id)
        occurrences = list(event.get("occurrences") or [])
        if index is None or len(occurrences) <= 1:
            await self._async_write(
                f"delete '{name}'", self.client.async_delete_event(bridge, event_id)
            )
            await self._async_reload_events(bridge)
            if any(str(e.get("id")) == event_id for e in self.events[bridge]):
                raise HomeAssistantError(f"The bridge did not delete '{name}'")
            return
        if not 0 <= index < len(occurrences):
            raise ServiceValidationError("That night is no longer part of the show")
        del occurrences[index]
        changed = dict(event, occurrences=occurrences, lastChanged=stamp(dt_util.utcnow()))
        await self._async_write(
            f"change '{name}'", self.client.async_update_event(bridge, event_id, changed)
        )
        await self._async_reload_events(bridge)
        stored = next((e for e in self.events[bridge] if str(e.get("id")) == event_id), None)
        if stored is None or len(stored.get("occurrences") or []) != len(occurrences):
            raise HomeAssistantError(f"The bridge did not keep the change to '{name}'")

    async def async_delete_event_named(self, bridge: str, name: str) -> None:
        """Remove a show by name. Refuses when the name is ambiguous."""
        await self._async_reload_events(bridge)
        await self.async_delete_event(bridge, str(self._show_named(bridge, name)["id"]))

    # ------------------------------------------------------------------ bridge

    async def async_sync_clock(self, bridge: str) -> None:
        """Set the bridge's clock to Home Assistant's and read it back."""
        await self._async_write(
            "set the bridge clock", self.client.async_set_time(bridge, stamp(dt_util.utcnow()))
        )
        try:
            bridge_time = parse_stamp(await self.client.async_get_time(bridge))
        except EverlightsCloudError as err:
            raise HomeAssistantError(f"Could not read the bridge clock back: {err}") from err
        if bridge_time is None:
            raise HomeAssistantError("The bridge did not report its clock")
        offset = round((bridge_time - dt_util.utcnow()).total_seconds(), 1)
        self._clock_offset[bridge] = offset
        data = dict(self.data)
        data["bridges"] = dict(data["bridges"])
        data["bridges"][bridge] = dict(data["bridges"][bridge], clockOffset=offset)
        self.async_set_updated_data(data)
        if abs(offset) > 30:
            raise HomeAssistantError(f"The bridge clock is still {offset:.0f} seconds off")

    async def async_reboot(self, bridge: str) -> None:
        """Restart a bridge. Nothing can confirm it but the bridge coming back."""
        await self._async_write("restart the bridge", self.client.async_reboot(bridge))
