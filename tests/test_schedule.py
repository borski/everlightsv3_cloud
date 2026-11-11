"""Pure schedule helpers."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.everlightsv3_cloud.schedule import (
    ALL_ZONES,
    build_event,
    expand,
    nightly_occurrences,
    occurrence_uid,
    parse_stamp,
    split_uid,
    stamp,
)

LA = ZoneInfo("America/Los_Angeles")


def test_stamp_is_utc_with_microseconds():
    assert stamp(datetime(2026, 12, 1, 17, 0, tzinfo=LA)) == "2026-12-02T01:00:00.000000Z"
    assert parse_stamp("2026-12-02T01:00:00.000Z") == datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    assert parse_stamp("nonsense") is None and parse_stamp(None) is None


def test_nights_keep_the_wall_clock_across_dst():
    # US clocks go back on 2026-11-01. 17:00 local is 00:00Z before and 01:00Z after.
    nights = nightly_occurrences(
        datetime(2026, 10, 31, 17, 0, tzinfo=LA), datetime(2026, 10, 31, 23, 0, tzinfo=LA), 2
    )
    assert [n["start"][:16] for n in nights] == ["2026-11-01T00:00", "2026-11-02T01:00"]


def test_nights_reject_bad_input():
    start = datetime(2026, 12, 1, 17, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        nightly_occurrences(start, start, 1)
    with pytest.raises(ValueError):
        nightly_occurrences(start, start.replace(hour=18), 0)


def test_build_event_shape():
    now = datetime(2026, 11, 11, tzinfo=UTC)
    event = build_event(
        "Show", [("seq-1", 20)], [ALL_ZONES], [{"start": "a", "end": "b"}],
        start_at_dusk=True, now=now,
    )
    assert set(event) == {
        "id", "alias", "group", "sequences", "zoneSerials", "occurrences", "flags", "lastChanged"
    }
    assert event["group"] == "User Created" and event["flags"] == ["StartAtDusk"]
    assert event["sequences"] == [{"sequenceId": "seq-1", "duration": 20}]


def test_uid_round_trip():
    assert split_uid(occurrence_uid("abc-def", 3)) == ("abc-def", 3)
    assert split_uid("abc-def") == ("abc-def", None)
    assert split_uid("abc#x") == ("abc#x", None)


def test_expand_skips_broken_nights_and_sorts():
    events = [
        {"id": "b", "occurrences": [{"start": "2026-12-02T01:00:00Z", "end": "2026-12-02T05:00:00Z"}]},
        {"id": "a", "flags": ["EndAtDawn"], "occurrences": [
            {"start": "bad", "end": "2026-12-01T05:00:00Z"},
            "junk",
            {"start": "2026-12-01T01:00:00Z", "end": "2026-12-01T01:00:00Z"},
        ]},
        {"id": "c"},
    ]
    rows = expand(events)
    assert [r["uid"] for r in rows] == ["a#2", "b#0"]
    assert rows[0]["dawn"] and not rows[0]["dusk"]
    assert rows[0]["end"] > rows[0]["start"]
