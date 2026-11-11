"""Adopt entities left behind by the local `everlights` integration.

The local integration cannot control bridges on firmware B5010 or newer. Its
entities (and the entity IDs that automations and dashboards point at) can be
moved to this integration instead of being recreated under new names.

Nothing is deleted. An entity is only adopted when:

* it belongs to the `everlights` platform and its zone is on this account,
* the old integration is not running it (disable the old entry first), and
* this integration has not already created its own entity for that zone.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, LEGACY_DOMAIN, LOGGER
from .entity import zone_device_info


@callback
def async_adopt_legacy_entities(
    hass: HomeAssistant, entry: ConfigEntry, zones: dict[str, dict[str, Any]]
) -> list[str]:
    """Move matching `everlights` registry entries to this entry. Returns entity IDs."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    adopted: list[str] = []
    skipped_loaded: list[str] = []

    for legacy in list(entity_registry.entities.values()):
        if legacy.platform != LEGACY_DOMAIN:
            continue
        serial = next(
            (s for s in zones if legacy.unique_id.startswith(f"{LEGACY_DOMAIN}_{s}_")), None
        )
        if serial is None:
            continue
        if entity_registry.async_get_entity_id(legacy.domain, DOMAIN, legacy.unique_id):
            continue
        state = hass.states.get(legacy.entity_id)
        if state is not None and state.state != STATE_UNKNOWN:
            skipped_loaded.append(legacy.entity_id)
            continue

        info = zone_device_info(zones[serial])
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers=info["identifiers"],
            name=info["name"],
            manufacturer=info["manufacturer"],
            model=info["model"],
            sw_version=info["sw_version"],
            hw_version=info["hw_version"],
            # The bridge device is registered before adoption runs.
            via_device=info["via_device"],
        )
        old_device = device_registry.async_get(legacy.device_id) if legacy.device_id else None
        if old_device is not None:
            # Carry over the room and any custom name. Never overwrite a choice.
            updates: dict[str, Any] = {}
            if old_device.area_id and not device.area_id:
                updates["area_id"] = old_device.area_id
            if old_device.name_by_user and not device.name_by_user:
                updates["name_by_user"] = old_device.name_by_user
            if updates:
                device_registry.async_update_device(device.id, **updates)

        try:
            # The unique ID stays exactly as it was.
            entity_registry.async_update_entity_platform(
                legacy.entity_id,
                DOMAIN,
                new_config_entry_id=entry.entry_id,
                new_device_id=device.id,
            )
        except ValueError as err:
            # Never let adoption stop the integration from loading.
            LOGGER.warning("Could not adopt %s: %s", legacy.entity_id, err)
            continue
        if legacy.disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY:
            # It was only disabled because the old config entry was disabled.
            entity_registry.async_update_entity(legacy.entity_id, disabled_by=None)
        adopted.append(legacy.entity_id)

    if adopted:
        LOGGER.info("Adopted %d entities from the local everlights integration: %s",
                    len(adopted), ", ".join(sorted(adopted)))
    if skipped_loaded:
        LOGGER.warning(
            "The local everlights integration is still running these entities, so they "
            "were not adopted and new ones were created instead: %s. To keep the old "
            "entity IDs, remove this integration, disable the old one, then add this "
            "one again",
            ", ".join(sorted(skipped_loaded)),
        )
    return adopted
