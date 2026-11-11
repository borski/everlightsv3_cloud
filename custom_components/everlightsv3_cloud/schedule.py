"""Pure helpers for the shows scheduled on a bridge.

A bridge event looks like this:

    {"id": uuid, "alias": name, "group": "User Created",
     "sequences": [{"sequenceId": id, "duration": seconds}, ...],
     "zoneSerials": [serial, ...],
     "occurrences": [{"start": iso, "end": iso}, ...],
     "flags": ["StartAtDusk", "EndAtDawn"], "lastChanged": iso}

Each occurrence is one night. While it runs, the bridge cycles through the
sequences, showing each for its duration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# What the app writes into zoneSerials to mean every zone on the bridge.
ALL_ZONES = "000000000000"
USER_GROUP = "User Created"
FLAG_DUSK = "StartAtDusk"
FLAG_DAWN = "EndAtDawn"
DEFAULT_DURATION = 20
MAX_OCCURRENCES = 366


def stamp(when: datetime) -> str:
    """Format a moment the way the app does: UTC, microseconds, trailing Z."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_stamp(value: Any) -> datetime | None:
    """Parse a bridge timestamp. Returns None for anything unreadable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def nightly_occurrences(start: datetime, end: datetime, nights: int) -> list[dict[str, str]]:
    """Repeat one start/end window for a number of consecutive nights.

    Days are added on the wall clock, so a show stays at the same local time
    across a daylight saving change.
    """
    if end <= start:
        raise ValueError("The end must be after the start")
    if not 1 <= nights <= MAX_OCCURRENCES:
        raise ValueError(f"Nights must be between 1 and {MAX_OCCURRENCES}")
    out = []
    for night in range(nights):
        shift = timedelta(days=night)
        out.append({"start": stamp(_shift(start, shift)), "end": stamp(_shift(end, shift))})
    return out


def _shift(when: datetime, by: timedelta) -> datetime:
    zone = when.tzinfo
    moved = when.replace(tzinfo=None) + by
    return moved.replace(tzinfo=zone)


def build_event(
    name: str,
    sequences: list[tuple[str, int]],
    zone_serials: list[str],
    occurrences: list[dict[str, str]],
    *,
    start_at_dusk: bool = False,
    end_at_dawn: bool = False,
    now: datetime,
) -> dict[str, Any]:
    """Build the body for a new event."""
    flags = [f for f, on in ((FLAG_DUSK, start_at_dusk), (FLAG_DAWN, end_at_dawn)) if on]
    return {
        "id": str(uuid.uuid4()),
        "alias": name,
        "group": USER_GROUP,
        "sequences": [{"sequenceId": sid, "duration": int(secs)} for sid, secs in sequences],
        "zoneSerials": list(zone_serials),
        "occurrences": occurrences,
        "flags": flags,
        "lastChanged": stamp(now),
    }


def occurrence_uid(event_id: str, index: int) -> str:
    """Calendar UID for one night of an event."""
    return f"{event_id}#{index}"


def split_uid(uid: str) -> tuple[str, int | None]:
    """Reverse occurrence_uid. A bare event id means the whole event."""
    event_id, _, index = uid.rpartition("#")
    if not event_id:
        return uid, None
    try:
        return event_id, int(index)
    except ValueError:
        return uid, None


def expand(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten events into one row per night, sorted by start."""
    rows = []
    for event in events:
        flags = event.get("flags") or []
        for index, occurrence in enumerate(event.get("occurrences") or []):
            if not isinstance(occurrence, dict):
                continue
            start, end = parse_stamp(occurrence.get("start")), parse_stamp(occurrence.get("end"))
            if start is None or end is None:
                continue
            if end <= start:
                end = start + timedelta(minutes=1)
            rows.append(
                {
                    "uid": occurrence_uid(str(event.get("id")), index),
                    "event": event,
                    "start": start,
                    "end": end,
                    "dusk": FLAG_DUSK in flags,
                    "dawn": FLAG_DAWN in flags,
                }
            )
    return sorted(rows, key=lambda r: r["start"])
