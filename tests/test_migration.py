"""Adopting entities from the local `everlights` integration."""
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.everlightsv3_cloud.const import DOMAIN, LEGACY_DOMAIN

from .conftest import BACK, GARAGE, find_device

KEYS = {"light": ["pattern"], "sensor": ["rssi", "snr", "lastRequestDate", "lastResponseDate"]}


def add_legacy(hass, disabled: bool = True) -> tuple[MockConfigEntry, dict[str, str]]:
    """Recreate what the local integration leaves in the registries."""
    legacy = MockConfigEntry(domain=LEGACY_DOMAIN, title="192.168.1.50", data={"host": "192.168.1.50"})
    legacy.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    ids: dict[str, str] = {}
    for serial, room in ((GARAGE, "Garage"), (BACK, "Back")):
        device = devices.async_get_or_create(
            config_entry_id=legacy.entry_id, identifiers={(LEGACY_DOMAIN, serial)}, name=serial
        )
        devices.async_update_device(device.id, name_by_user=room, area_id=f"area_{room.lower()}")
        for domain, keys in KEYS.items():
            for key in keys:
                entry = entities.async_get_or_create(
                    domain, LEGACY_DOMAIN, f"everlights_{serial}_{key}",
                    config_entry=legacy, device_id=device.id,
                    suggested_object_id=f"everlights_{room.lower()}_{key}".removesuffix("_pattern"),
                    disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY if disabled else None,
                )
                ids[f"{serial}_{key}"] = entry.entity_id
    entities.async_update_entity(ids[f"{GARAGE}_pattern"], name="Garage roofline", icon="mdi:home-roof")
    return legacy, ids


async def test_adopts_disabled_legacy_entities(hass, cloud, mock_config_entry):
    legacy, ids = add_legacy(hass)
    assert ids[f"{GARAGE}_pattern"] == "light.everlights_garage"
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entities, devices = er.async_get(hass), dr.async_get(hass)
    for key, entity_id in ids.items():
        entry = entities.async_get(entity_id)
        assert entry.platform == DOMAIN
        assert entry.config_entry_id == mock_config_entry.entry_id
        assert entry.unique_id == f"everlights_{key}"
        assert entry.disabled_by is None
        assert hass.states.get(entity_id) is not None
    # Same entity ID, now live, and the user's customisation survived.
    light = hass.states.get("light.everlights_garage")
    assert light.state == "off"
    assert light.attributes["friendly_name"] == "Garage roofline"
    assert light.attributes["icon"] == "mdi:home-roof"
    # No duplicate was created for an adopted zone.
    assert hass.states.get("light.garage_everlights") is None
    # The zone with no legacy entities is created normally.
    assert hass.states.get("light.front_everlights").state == "off"
    # Room and custom device name carried over.
    device = find_device(devices, (DOMAIN, GARAGE), mock_config_entry.entry_id)
    assert device.area_id == "area_garage" and device.name_by_user == "Garage"
    # Nothing was deleted from the old integration.
    assert hass.config_entries.async_get_entry(legacy.entry_id) is not None
    assert find_device(devices, (LEGACY_DOMAIN, GARAGE), legacy.entry_id) is not None


async def test_adoption_is_idempotent(hass, cloud, mock_config_entry):
    add_legacy(hass)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    before = sorted(e.entity_id for e in er.async_get(hass).entities.values())
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert sorted(e.entity_id for e in er.async_get(hass).entities.values()) == before


async def test_running_legacy_entities_are_left_alone(hass, cloud, mock_config_entry, caplog):
    legacy, ids = add_legacy(hass, disabled=False)
    for entity_id in ids.values():
        hass.states.async_set(entity_id, "unavailable")  # The old integration still owns them.
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_get(hass)
    assert all(entities.async_get(e).platform == LEGACY_DOMAIN for e in ids.values())
    assert hass.states.get("light.garage_everlights").state == "off"
    assert "were not adopted" in caplog.text
