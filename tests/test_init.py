"""Setup, availability, and failure handling."""
from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.everlightsv3_cloud.const import DOMAIN

from .conftest import BACK, BRIDGE, FRONT, GARAGE, STRAY, find_device


async def poll(hass, freezer, seconds=90):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_setup_creates_entities(hass, setup_entry):
    assert setup_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    for serial, alias in ((GARAGE, "garage"), (FRONT, "front"), (BACK, "back")):
        light = registry.async_get_entity_id("light", DOMAIN, f"everlights_{serial}_pattern")
        assert light == f"light.{alias}_everlights"
        assert hass.states.get(light).state == "off"
        for key in ("rssi", "snr", "lastRequestDate", "lastResponseDate"):
            assert registry.async_get_entity_id("sensor", DOMAIN, f"everlights_{serial}_{key}")
    assert not [e for e in registry.entities.values() if STRAY in e.unique_id]
    assert hass.states.get("sensor.back_signal_strength").state == "-82"
    assert hass.states.get("sensor.back_last_response_date").state == "2026-11-11T06:53:44+00:00"

    devices = dr.async_get(hass)
    bridge = find_device(devices, (DOMAIN, BRIDGE), setup_entry.entry_id)
    zone = find_device(devices, (DOMAIN, GARAGE), setup_entry.entry_id)
    assert bridge.name == "EverLights Bridge" and bridge.sw_version == "B5010"
    assert zone.name == "Garage" and zone.via_device_id == bridge.id


async def test_effect_list_comes_from_bridge(hass, setup_entry, cloud):
    effects = hass.states.get("light.garage_everlights").attributes["effect_list"]
    assert effects[0] == "off"
    assert len(effects) == len(cloud.library) + 1


async def test_absent_fields(hass, cloud, mock_config_entry):
    for zone in cloud.status["zones"]:
        for key in ("rssi", "snr", "lastRequestDate", "lastResponseDate", "firmwareVersion", "hardwareVersion"):
            zone.pop(key, None)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.garage_signal_strength").state == "unknown"
    assert hass.states.get("sensor.garage_last_response_date").state == "unknown"
    # No timestamp means we cannot call it stale. The light stays usable.
    assert hass.states.get("light.garage_everlights").state == "off"


async def test_zone_missing_from_bridge(hass, cloud, mock_config_entry):
    cloud.status["zones"] = [z for z in cloud.status["zones"] if z["serial"] != GARAGE]
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("light.garage_everlights").state == "unavailable"
    assert hass.states.get("light.front_everlights").state == "off"


async def test_stale_zone_is_unavailable(hass, setup_entry, cloud, freezer):
    # The bridge keeps answering but one zone stopped responding to it.
    await poll(hass, freezer, seconds=20 * 60)
    for zone in cloud.status["zones"]:
        if zone["serial"] != GARAGE:
            zone["lastResponseDate"] = "2026-11-11T07:14:00.000Z"
    await poll(hass, freezer)
    assert hass.states.get("light.garage_everlights").state == "unavailable"
    assert hass.states.get("light.front_everlights").state == "off"
    # Sensors keep reporting so the stale timestamp is visible.
    assert hass.states.get("sensor.garage_last_response_date").state == "2026-11-11T06:53:46+00:00"


async def test_cloud_outage_then_recovery(hass, setup_entry, cloud, freezer):
    cloud.queue("status", 503, 503, 503)
    await poll(hass, freezer)
    assert hass.states.get("light.garage_everlights").state == "unavailable"
    assert hass.states.get("sensor.garage_signal_strength").state == "unavailable"
    await poll(hass, freezer)
    assert hass.states.get("light.garage_everlights").state == "off"


async def test_outage_at_startup_retries(hass, cloud, mock_config_entry):
    cloud.queue("token", TimeoutError)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_tunnel_gone_at_startup_retries(hass, cloud, mock_config_entry):
    cloud.queue("status", (410, {"error": "gone", "cloud": True}))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_expired_credentials_start_reauth(hass, setup_entry, cloud, freezer):
    cloud.password = "changed-in-the-app"
    cloud.refresh_tokens.clear()
    cloud.access_tokens.clear()
    cloud.remote_tokens.clear()  # The bridge token has to be minted again.
    await poll(hass, freezer)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert hass.states.get("light.garage_everlights").state == "unavailable"


async def test_bad_password_at_startup(hass, cloud, mock_config_entry):
    cloud.password = "changed-in-the-app"
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload(hass, setup_entry):
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacted(hass, setup_entry):
    from custom_components.everlightsv3_cloud.diagnostics import async_get_config_entry_diagnostics

    text = str(await async_get_config_entry_diagnostics(hass, setup_entry))
    for secret in ("correct-horse", "user@example.com", GARAGE, BRIDGE, "Garage"):
        assert secret not in text
    assert "N3063" in text
