"""Everlights v3 Cloud: control Everlights bridges through the vendor cloud.

Bridge firmware B5010 removed the local control API, which broke the local
`everlights` integration. This one uses the same cloud path as the official
app.
"""
from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EverlightsCloudClient
from .const import DOMAIN
from .coordinator import EverlightsCloudConfigEntry, EverlightsCloudCoordinator
from .migration import async_adopt_legacy_entities

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: EverlightsCloudConfigEntry) -> bool:
    """Set up from a config entry."""
    client = EverlightsCloudClient(
        async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )
    coordinator = EverlightsCloudCoordinator(hass, entry, client)
    # Raises ConfigEntryNotReady (retry later) or ConfigEntryAuthFailed (reauth).
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    device_registry = dr.async_get(hass)
    for serial, bridge in coordinator.data["bridges"].items():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, serial)},
            manufacturer="EverLights",
            model="Bridge",
            name=bridge["alias"],
            sw_version=bridge.get("firmwareVersion"),
            hw_version=bridge.get("hardwareVersion"),
        )
    async_adopt_legacy_entities(hass, entry, coordinator.data["zones"])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EverlightsCloudConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
