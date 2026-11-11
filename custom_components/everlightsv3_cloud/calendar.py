"""Calendar of the shows scheduled on an Everlights bridge."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_DELETE_SHOW, SERVICE_SCHEDULE_SHOW
from .coordinator import EverlightsCloudConfigEntry
from .entity import EverlightsCloudBridgeEntity
from .schedule import ALL_ZONES, MAX_OCCURRENCES, expand

ENTITY_DESCRIPTION = CalendarEntityDescription(
    key="schedule", name="Schedule", icon="mdi:calendar-star"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EverlightsCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one calendar per bridge."""
    coordinator = entry.runtime_data
    async_add_entities(
        EverlightsCloudCalendar(coordinator, ENTITY_DESCRIPTION, serial)
        for serial in coordinator.data["bridges"]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SCHEDULE_SHOW,
        {
            vol.Required("name"): cv.string,
            vol.Required("sequences"): vol.Any(
                vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
                vol.All({cv.string: vol.All(vol.Coerce(int), vol.Range(1, 86400))}, vol.Length(min=1)),
            ),
            vol.Required("start"): cv.datetime,
            vol.Required("end"): cv.datetime,
            vol.Optional("nights", default=1): vol.All(vol.Coerce(int), vol.Range(1, MAX_OCCURRENCES)),
            vol.Optional("start_at_dusk", default=False): cv.boolean,
            vol.Optional("end_at_dawn", default=False): cv.boolean,
            vol.Optional("lights"): cv.entity_ids,
        },
        "async_schedule_show",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_SHOW, {vol.Required("name"): cv.string}, "async_delete_show"
    )


class EverlightsCloudCalendar(EverlightsCloudBridgeEntity, CalendarEntity):
    """Shows the bridge runs on its own, one calendar entry per night.

    New shows are made with the create_event action, because a show needs
    sequences and zones that the calendar dialog has no fields for.
    """

    _attr_supported_features = CalendarEntityFeature.DELETE_EVENT

    def _rows(self) -> list[dict[str, Any]]:
        return expand(self.coordinator.events.get(self.serial, []))

    def _describe(self, row: dict[str, Any]) -> CalendarEvent:
        event = row["event"]
        names = {s.get("id"): s.get("alias") for s in self.coordinator.sequences}
        zones = self.coordinator.data["zones"]
        lines = []
        for item in event.get("sequences") or []:
            if not isinstance(item, dict):
                continue
            label = names.get(item.get("sequenceId")) or "Unknown sequence"
            lines.append(f"{label} for {item.get('duration')} seconds")
        serials = event.get("zoneSerials") or []
        if ALL_ZONES in serials or not serials:
            where = "All zones"
        else:
            where = ", ".join(zones.get(z, {}).get("alias") or "Unknown zone" for z in serials)
        notes = [n for n, on in (("Starts at dusk", row["dusk"]), ("Ends at dawn", row["dawn"])) if on]
        return CalendarEvent(
            start=dt_util.as_local(row["start"]),
            end=dt_util.as_local(row["end"]),
            summary=str(event.get("alias") or "Everlights show"),
            description="\n".join([*lines, *notes]) or None,
            location=where,
            uid=row["uid"],
        )

    @property
    def event(self) -> CalendarEvent | None:
        """The show running now, or the next one."""
        now = dt_util.utcnow()
        for row in self._rows():
            if row["end"] > now:
                return self._describe(row)
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the nights that overlap a time range."""
        return [
            self._describe(row)
            for row in self._rows()
            if row["end"] > start_date and row["start"] < end_date
        ]

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete one night. Deleting the last night removes the show."""
        if not uid:
            raise ServiceValidationError("That show can't be identified")
        await self.coordinator.async_delete_event(self.serial, uid)

    # ------------------------------------------------------------- services

    async def async_schedule_show(
        self,
        name: str,
        sequences: list[str] | dict[str, int],
        start: datetime,
        end: datetime,
        nights: int = 1,
        start_at_dusk: bool = False,
        end_at_dawn: bool = False,
        lights: list[str] | None = None,
    ) -> None:
        """Schedule a show on this bridge. Without lights it covers every zone."""
        mine = [
            z for z, info in self.coordinator.data["zones"].items()
            if info.get("bridgeSerial") == self.serial
        ]
        if lights:
            registry = er.async_get(self.hass)
            serials = []
            for entity_id in lights:
                entry = registry.async_get(entity_id)
                serial = next(
                    (z for z in mine if entry and entry.platform == DOMAIN and f"_{z}_" in entry.unique_id),
                    None,
                )
                if serial is None:
                    raise ServiceValidationError(
                        f"{entity_id} is not an Everlights light on this bridge"
                    )
                serials.append(serial)
        else:
            serials = mine
        pairs: list[tuple[str, int | None]] = (
            list(sequences.items()) if isinstance(sequences, dict) else [(n, None) for n in sequences]
        )
        zone = dt_util.DEFAULT_TIME_ZONE
        start = start if start.tzinfo else start.replace(tzinfo=zone)
        end = end if end.tzinfo else end.replace(tzinfo=zone)
        await self.coordinator.async_create_event(
            serials, name, pairs, start, end,
            nights=nights, start_at_dusk=start_at_dusk, end_at_dawn=end_at_dawn,
        )

    async def async_delete_show(self, name: str) -> None:
        """Delete a show, with all its nights, by name."""
        await self.coordinator.async_delete_event_named(self.serial, name)
