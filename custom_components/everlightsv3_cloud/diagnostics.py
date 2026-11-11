"""Diagnostics for Everlights v3 Cloud."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import EverlightsCloudConfigEntry

TO_REDACT = {
    "username", "password", "serial", "bridgeSerial", "localIp", "publicIp", "ssid",
    "alias", "title", "unique_id", "zoneSerials", "id", "sequenceId", "email",
    "firstname", "lastname",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EverlightsCloudConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with account, network, and serial details removed."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "sequence_count": len(coordinator.sequences),
        "events": [
            async_redact_data(e, TO_REDACT) for events in coordinator.events.values() for e in events
        ],
        "bridges": [async_redact_data(b, TO_REDACT) for b in data.get("bridges", {}).values()],
        "zones": [async_redact_data(z, TO_REDACT) for z in data.get("zones", {}).values()],
    }
