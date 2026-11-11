"""Light commands: what gets sent, and that nothing is reported without confirmation."""
import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import FRONT, GARAGE
from .test_init import poll

ENTITY = "light.garage_everlights"


async def turn_on(hass, **data):
    await hass.services.async_call("light", "turn_on", {"entity_id": ENTITY, **data}, blocking=True)


async def turn_off(hass):
    await hass.services.async_call("light", "turn_off", {"entity_id": ENTITY}, blocking=True)


async def test_turn_on_colour(hass, setup_entry, cloud):
    await turn_on(hass, hs_color=(0, 100))
    assert cloud.posts == [(GARAGE, {"pattern": ["FF0000"], "effects": []})]
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["hs_color"] == (0.0, 100.0)
    assert state.attributes["brightness"] == 255
    assert state.attributes["effect"] == "off"
    # Only the addressed zone was touched.
    assert cloud.sequences[FRONT] == {"pattern": [], "effects": []}


async def test_bare_turn_on_defaults_to_white(hass, setup_entry, cloud):
    await turn_on(hass)
    assert cloud.posts[-1][1] == {"pattern": ["FFFFFF"], "effects": []}


async def test_brightness_scales_colours(hass, setup_entry, cloud):
    await turn_on(hass, hs_color=(0, 100), brightness=128)
    assert cloud.posts[-1][1]["pattern"] == ["800000"]
    state = hass.states.get(ENTITY)
    assert state.attributes["brightness"] == 128
    assert state.attributes["hs_color"] == (0.0, 100.0)
    # Brightness alone keeps the colour.
    await turn_on(hass, brightness=64)
    assert cloud.posts[-1][1]["pattern"] == ["400000"]
    assert hass.states.get(ENTITY).attributes["hs_color"] == (0.0, 100.0)


async def test_effect(hass, setup_entry, cloud):
    sequence = cloud.library[1]  # Six colours with a chase effect.
    await turn_on(hass, effect=sequence["alias"])
    assert cloud.posts[-1][1] == {"pattern": sequence["pattern"], "effects": sequence["effects"]}
    assert hass.states.get(ENTITY).attributes["effect"] == sequence["alias"]
    # Dimming an effect scales every colour and keeps the effect.
    await turn_on(hass, brightness=128)
    assert cloud.posts[-1][1]["pattern"][0] == "800000"
    assert cloud.posts[-1][1]["effects"] == sequence["effects"]
    assert hass.states.get(ENTITY).attributes["effect"] == sequence["alias"]
    # Picking a colour drops the effect.
    await turn_on(hass, hs_color=(120, 100))
    assert cloud.posts[-1][1] == {"pattern": ["008000"], "effects": []}
    assert hass.states.get(ENTITY).attributes["effect"] == "off"


async def test_unknown_effect_sends_nothing(hass, setup_entry, cloud):
    with pytest.raises(ServiceValidationError):
        await turn_on(hass, effect="No Such Thing")
    assert cloud.posts == []


async def test_off_then_on_restores(hass, setup_entry, cloud):
    await turn_on(hass, hs_color=(240, 100), brightness=100)
    await turn_off(hass)
    assert cloud.posts[-1][1] == {"pattern": [], "effects": []}
    assert hass.states.get(ENTITY).state == "off"
    await turn_on(hass)
    assert cloud.posts[-1][1]["pattern"] == ["000064"]


async def test_accepted_but_not_applied(hass, setup_entry, cloud):
    cloud.ignore_writes = True
    with pytest.raises(HomeAssistantError, match="did not change"):
        await turn_on(hass, hs_color=(0, 100))
    assert len(cloud.posts) == 1
    assert hass.states.get(ENTITY).state == "off"


async def test_timeout_is_checked_not_replayed(hass, setup_entry, cloud):
    # The write times out and never reached the bridge.
    cloud.queue("sequence", TimeoutError)
    with pytest.raises(HomeAssistantError, match="did not change"):
        await turn_on(hass, hs_color=(0, 100))
    assert len(cloud.posts) == 1
    assert hass.states.get(ENTITY).state == "off"


async def test_timeout_that_actually_applied(hass, setup_entry, cloud):
    # The answer was lost but the bridge did change. The read-back finds out.
    cloud.sequences[GARAGE] = {"pattern": ["FF0000"], "effects": []}
    cloud.ignore_writes = True
    cloud.queue("sequence", TimeoutError)
    await turn_on(hass, hs_color=(0, 100))
    assert len(cloud.posts) == 1
    assert hass.states.get(ENTITY).state == "on"


async def test_rejected_command(hass, setup_entry, cloud):
    cloud.queue("sequence", 500)
    with pytest.raises(HomeAssistantError, match="failed"):
        await turn_on(hass, hs_color=(0, 100))
    assert len(cloud.posts) == 1
    assert hass.states.get(ENTITY).state == "off"


async def test_change_from_the_app_is_recognised(hass, setup_entry, cloud, freezer):
    sequence = cloud.library[1]
    cloud.sequences[GARAGE] = {"pattern": [c.lower() for c in sequence["pattern"]],
                               "effects": sequence["effects"]}
    await poll(hass, freezer)
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["effect"] == sequence["alias"]
    assert state.attributes["brightness"] == 255
    # A dimmed solid colour that is in no library.
    cloud.sequences[GARAGE] = {"pattern": ["003B80"], "effects": []}
    await poll(hass, freezer)
    state = hass.states.get(ENTITY)
    assert state.attributes["effect"] == "off"
    assert state.attributes["brightness"] == 128
    assert cloud.posts == []
