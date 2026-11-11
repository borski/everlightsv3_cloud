"""Sequence library, schedule, and bridge maintenance. Every write is sent once and read back."""
from datetime import datetime

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.everlightsv3_cloud.const import DOMAIN

from .conftest import BRIDGE, FRONT, GARAGE
from .test_init import poll

LIGHT = "light.garage_everlights"
CALENDAR = "calendar.everlights_bridge_schedule"
OWN = "Sequence 098"  # Personal, two colours, no effects.
BUILT_IN = "Sequence 001"


async def call(hass, service, entity_id=LIGHT, **data):
    await hass.services.async_call(DOMAIN, service, {"entity_id": entity_id, **data}, blocking=True)


def writes(cloud, label):
    return [c for c in cloud.calls if c[1] == label and c[0] != "GET"]


# ------------------------------------------------------------------ clear

async def test_clear_hands_back_to_schedule(hass, setup_entry, cloud):
    cloud.sequences[GARAGE] = {"pattern": ["FF0000"], "effects": []}
    cloud.scheduled = {"pattern": ["00FF00"], "effects": []}
    await call(hass, "clear")
    assert writes(cloud, "sequence") == [("DELETE", "sequence")]
    state = hass.states.get(LIGHT)
    assert state.state == "on" and state.attributes["hs_color"] == (120.0, 100.0)


async def test_clear_with_nothing_scheduled_goes_off(hass, setup_entry, cloud):
    cloud.sequences[GARAGE] = {"pattern": ["FF0000"], "effects": []}
    await call(hass, "clear")
    assert hass.states.get(LIGHT).state == "off"


async def test_clear_without_answer_is_not_replayed(hass, setup_entry, cloud):
    cloud.queue("sequence", TimeoutError)
    with pytest.raises(HomeAssistantError, match="no answer"):
        await call(hass, "clear")
    assert writes(cloud, "sequence") == [("DELETE", "sequence")]


# ---------------------------------------------------------------- library

async def test_save_explicit_colours(hass, setup_entry, cloud):
    await call(hass, "save_sequence", name="Candy", colors=["ff0000", "#FFFFFF"], effects={"chase": 100})
    saved = cloud.library[-1]
    assert saved["alias"] == "Candy" and saved["groups"] == ["Personal/"]
    assert saved["pattern"] == ["FF0000", "FFFFFF"] and saved["colorMode"] == "rgb"
    assert saved["effects"] == [{"effectType": "chase", "value": 100}]
    assert "Candy" in hass.states.get(LIGHT).attributes["effect_list"]


async def test_save_what_the_light_shows_at_full_brightness(hass, setup_entry, cloud):
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "effect": BUILT_IN, "brightness": 128}, blocking=True
    )
    await call(hass, "save_sequence", name="Mine")
    assert cloud.library[-1]["pattern"] == cloud.library[1]["pattern"]
    assert cloud.library[-1]["effects"] == cloud.library[1]["effects"]


async def test_save_refuses_duplicate_name_and_off_light(hass, setup_entry, cloud):
    with pytest.raises(ServiceValidationError, match="already exists"):
        await call(hass, "save_sequence", name=OWN.lower(), colors=["FF0000"])
    with pytest.raises(ServiceValidationError, match="off"):
        await call(hass, "save_sequence", name="Fresh")
    assert writes(cloud, "sequences") == []


async def test_save_that_the_bridge_dropped(hass, setup_entry, cloud):
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not save"):
        await call(hass, "save_sequence", name="Ghost", colors=["FF0000"])
    assert len(writes(cloud, "sequences")) == 1


async def test_save_without_answer_that_landed(hass, setup_entry, cloud):
    # The answer is lost. The write is not repeated, and the read-back decides.
    cloud.queue("sequences", None, TimeoutError)
    with pytest.raises(HomeAssistantError, match="did not save"):
        await call(hass, "save_sequence", name="Lost", colors=["FF0000"])
    assert len(writes(cloud, "sequences")) == 1


async def test_update_sequence(hass, setup_entry, cloud):
    before = len(cloud.library)
    await call(hass, "update_sequence", name=OWN, colors=["0000FF"], new_name="Blue")
    changed = next(s for s in cloud.library if s["id"] == "seq-098")
    assert changed["alias"] == "Blue" and changed["pattern"] == ["0000FF"]
    assert changed["groups"] == ["Personal/"] and len(cloud.library) == before
    assert writes(cloud, "sequences") == [("PUT", "sequences")]


async def test_update_refusals(hass, setup_entry, cloud):
    with pytest.raises(ServiceValidationError, match="built-in"):
        await call(hass, "update_sequence", name=BUILT_IN, colors=["0000FF"])
    with pytest.raises(ServiceValidationError, match="No sequence"):
        await call(hass, "update_sequence", name="Nope", colors=["0000FF"])
    with pytest.raises(ServiceValidationError, match="Nothing to change"):
        await call(hass, "update_sequence", name=OWN)
    with pytest.raises(ServiceValidationError, match="already exists"):
        await call(hass, "update_sequence", name=OWN, new_name=BUILT_IN)
    cloud.library[99]["alias"] = OWN
    with pytest.raises(ServiceValidationError, match="More than one"):
        await call(hass, "update_sequence", name=OWN, colors=["0000FF"])
    assert writes(cloud, "sequences") == []


async def test_update_that_the_bridge_dropped(hass, setup_entry, cloud):
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not keep"):
        await call(hass, "update_sequence", name=OWN, colors=["0000FF"])


async def test_delete_sequence(hass, setup_entry, cloud):
    await call(hass, "delete_sequence", name=OWN)
    assert not any(s["id"] == "seq-098" for s in cloud.library)
    assert OWN not in hass.states.get(LIGHT).attributes["effect_list"]


async def test_delete_refusals(hass, setup_entry, cloud):
    with pytest.raises(ServiceValidationError, match="built-in"):
        await call(hass, "delete_sequence", name=BUILT_IN)
    with pytest.raises(ServiceValidationError, match="No sequence"):
        await call(hass, "delete_sequence", name="Nope")
    assert writes(cloud, "sequences") == []
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not delete"):
        await call(hass, "delete_sequence", name=OWN)


# --------------------------------------------------------------- schedule

SHOW = {
    "name": "Holiday",
    "sequences": {BUILT_IN: 30, OWN: 45},
    "start": "2026-12-01 17:00:00",
    "end": "2026-12-01 23:00:00",
}


async def test_schedule_show_for_every_zone(hass, setup_entry, cloud):
    await call(hass, "schedule_show", CALENDAR, **SHOW, nights=3, start_at_dusk=True)
    (event,) = cloud.events
    assert event["alias"] == "Holiday" and event["group"] == "User Created"
    assert event["zoneSerials"] == ["000000000000"] and event["flags"] == ["StartAtDusk"]
    assert event["sequences"] == [
        {"sequenceId": "seq-001", "duration": 30}, {"sequenceId": "seq-098", "duration": 45}
    ]
    assert len(event["occurrences"]) == 3
    # The test instance runs in US/Pacific, so 17:00 local is 01:00Z.
    assert event["occurrences"][0]["start"] == "2026-12-02T01:00:00.000000Z"
    assert hass.states.get("sensor.everlights_bridge_scheduled_shows").state == "1"


async def test_schedule_show_for_chosen_lights(hass, setup_entry, cloud):
    await call(
        hass, "schedule_show", CALENDAR,
        **{**SHOW, "sequences": [OWN]}, lights=[LIGHT, "light.front_everlights"],
    )
    assert cloud.events[0]["zoneSerials"] == sorted([GARAGE, FRONT])
    assert cloud.events[0]["sequences"] == [{"sequenceId": "seq-098", "duration": 20}]


async def test_schedule_refusals(hass, setup_entry, cloud):
    with pytest.raises(ServiceValidationError, match="No sequence"):
        await call(hass, "schedule_show", CALENDAR, **{**SHOW, "sequences": ["Nope"]})
    with pytest.raises(ServiceValidationError, match="after the start"):
        await call(hass, "schedule_show", CALENDAR, **{**SHOW, "end": SHOW["start"]})
    with pytest.raises(ServiceValidationError, match="not an Everlights light"):
        await call(hass, "schedule_show", CALENDAR, **SHOW, lights=["light.kitchen"])
    assert writes(cloud, "events") == []
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not schedule"):
        await call(hass, "schedule_show", CALENDAR, **SHOW)
    assert len(writes(cloud, "events")) == 1


async def test_calendar_lists_nights(hass, setup_entry, cloud, hass_client=None):
    await call(hass, "schedule_show", CALENDAR, **SHOW, nights=2, end_at_dawn=True)
    state = hass.states.get(CALENDAR)
    assert state.attributes["message"] == "Holiday"
    assert state.attributes["location"] == "All zones"
    assert "Sequence 001 for 30 seconds" in state.attributes["description"]
    assert "Ends at dawn" in state.attributes["description"]
    result = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": CALENDAR, "start_date_time": "2026-12-01 00:00:00",
         "end_date_time": "2026-12-02 12:00:00"},
        blocking=True, return_response=True,
    )
    assert len(result[CALENDAR]["events"]) == 1


async def test_schedule_made_in_the_app_shows_up(hass, setup_entry, cloud, freezer):
    cloud.events.append({
        "id": "ev-1", "alias": "From App", "group": "User Created", "flags": [],
        "sequences": [{"sequenceId": "gone", "duration": 10}], "zoneSerials": [GARAGE],
        "occurrences": [{"start": "2026-11-12T01:00:00.000Z", "end": "2026-11-12T07:00:00.000Z"}],
    })
    await poll(hass, freezer, seconds=400)
    state = hass.states.get(CALENDAR)
    assert state.attributes["message"] == "From App"
    assert state.attributes["location"] == "Garage"
    assert "Unknown sequence" in state.attributes["description"]


async def test_delete_one_night_then_the_show(hass, setup_entry, cloud):
    await call(hass, "schedule_show", CALENDAR, **SHOW, nights=2)
    event_id = cloud.events[0]["id"]
    coordinator = setup_entry.runtime_data
    await coordinator.async_delete_event(BRIDGE, f"{event_id}#0")
    assert len(cloud.events[0]["occurrences"]) == 1
    assert cloud.events[0]["occurrences"][0]["start"].startswith("2026-12-03")
    assert writes(cloud, "events")[-1] == ("PUT", "events")
    # The last night takes the show with it.
    await coordinator.async_delete_event(BRIDGE, f"{event_id}#0")
    assert cloud.events == []
    assert writes(cloud, "events")[-1] == ("DELETE", "events")
    with pytest.raises(ServiceValidationError, match="no longer"):
        await coordinator.async_delete_event(BRIDGE, f"{event_id}#0")


async def test_delete_show_by_name(hass, setup_entry, cloud):
    await call(hass, "schedule_show", CALENDAR, **SHOW)
    with pytest.raises(ServiceValidationError, match="No show"):
        await call(hass, "delete_show", CALENDAR, name="Other")
    await call(hass, "delete_show", CALENDAR, name="holiday")
    assert cloud.events == []
    await call(hass, "schedule_show", CALENDAR, **SHOW)
    await call(hass, "schedule_show", CALENDAR, **SHOW)
    with pytest.raises(ServiceValidationError, match="More than one"):
        await call(hass, "delete_show", CALENDAR, name="Holiday")
    cloud.events.pop()
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not delete"):
        await call(hass, "delete_show", CALENDAR, name="Holiday")


async def test_schedule_outage_does_not_break_lights(hass, cloud, mock_config_entry):
    cloud.queue("events", 500, 500, 500)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == "off"
    assert hass.states.get(CALENDAR).state == "off"


# ----------------------------------------------------------------- bridge

async def enable(hass, entity_id, entry):
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_bridge_entities(hass, setup_entry, cloud):
    assert hass.states.get("binary_sensor.everlights_bridge_cloud_connection").state == "on"
    assert hass.states.get("sensor.everlights_bridge_last_check_in").state == "2026-11-11T06:53:56+00:00"
    registry = er.async_get(hass)
    for entity_id in ("button.everlights_bridge_restart", "button.everlights_bridge_sync_clock",
                      "sensor.everlights_bridge_clock_offset", "sensor.garage_voltage_raw"):
        assert registry.async_get(entity_id).disabled_by is not None, entity_id


async def test_connection_goes_off_in_an_outage(hass, setup_entry, cloud, freezer):
    cloud.queue("status", 500, 500, 500)
    await poll(hass, freezer)
    assert hass.states.get("binary_sensor.everlights_bridge_cloud_connection").state == "off"
    await poll(hass, freezer)
    assert hass.states.get("binary_sensor.everlights_bridge_cloud_connection").state == "on"


async def test_sync_clock(hass, setup_entry, cloud):
    cloud.clock_skew = 500
    await enable(hass, "button.everlights_bridge_sync_clock", setup_entry)
    await enable(hass, "sensor.everlights_bridge_clock_offset", setup_entry)
    assert float(hass.states.get("sensor.everlights_bridge_clock_offset").state) == pytest.approx(500, abs=2)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.everlights_bridge_sync_clock"}, blocking=True
    )
    assert abs(cloud.clock_skew) < 2
    assert writes(cloud, "time") == [("PUT", "time")]
    assert abs(float(hass.states.get("sensor.everlights_bridge_clock_offset").state)) < 2
    cloud.clock_skew, cloud.ignore_writes = 500, True
    with pytest.raises(HomeAssistantError, match="still"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.everlights_bridge_sync_clock"}, blocking=True
        )


async def test_restart_is_sent_once(hass, setup_entry, cloud):
    await enable(hass, "button.everlights_bridge_restart", setup_entry)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.everlights_bridge_restart"}, blocking=True
    )
    assert cloud.reboots == 1
    cloud.queue("reboot", TimeoutError)
    # No answer is normal for a restart. It is logged, not repeated.
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.everlights_bridge_restart"}, blocking=True
    )
    assert cloud.count("GET", "reboot") == 2 and cloud.reboots == 1
    cloud.queue("reboot", 500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.everlights_bridge_restart"}, blocking=True
        )
    assert cloud.count("GET", "reboot") == 3


async def test_local_naive_times_use_the_ha_zone(hass, setup_entry, cloud):
    times = {"start": datetime(2026, 12, 1, 17), "end": datetime(2026, 12, 1, 23)}
    await call(hass, "schedule_show", CALENDAR, **{**SHOW, **times})
    assert cloud.events[0]["occurrences"][0]["end"] == "2026-12-02T07:00:00.000000Z"
