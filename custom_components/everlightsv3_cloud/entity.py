"""Base entity for Everlights v3 Cloud."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EverlightsCloudCoordinator


def zone_unique_id(serial: str, key: str) -> str:
    """Unique ID for a zone entity.

    Deliberately identical to the local `everlights` integration's format so
    its registry entries can be adopted one to one.
    """
    return f"everlights_{serial}_{key}"


def via_bridge(hass: HomeAssistant, entry_id: str, bridge_serial: str) -> dict[str, Any]:
    """Link a zone device to its bridge in the way this Home Assistant version expects."""
    parent = (DOMAIN, bridge_serial)
    registry = dr.async_get(hass)
    lookup = getattr(registry, "async_get_device_by_identifier", None)
    if lookup is None:
        return {"via_device": parent}
    # Home Assistant 2026.8 and newer link devices by registry id.
    bridge = lookup(parent, entry_id)
    return {"via_device_id": bridge.id} if bridge is not None else {}


def zone_device_info(hass: HomeAssistant, entry_id: str, zone: dict[str, Any]) -> DeviceInfo:
    """Device info for a zone (one device per light controller)."""
    hardware = zone.get("hardwareVersion")
    info = DeviceInfo(
        identifiers={(DOMAIN, zone["serial"])},
        name=zone.get("alias") or zone["serial"],
        manufacturer="EverLights",
        model=zone.get("model") or "Controller",
        sw_version=zone.get("firmwareVersion"),
        hw_version=str(hardware) if hardware is not None else None,
    )
    info.update(via_bridge(hass, entry_id, zone["bridgeSerial"]))  # type: ignore[typeddict-item]
    return info


class EverlightsCloudEntity(CoordinatorEntity[EverlightsCloudCoordinator]):
    """An entity that belongs to one zone."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EverlightsCloudCoordinator,
        entity_description: EntityDescription,
        serial: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self.serial = serial
        self._attr_unique_id = zone_unique_id(serial, entity_description.key)
        self._attr_device_info = zone_device_info(
            coordinator.hass, coordinator.config_entry.entry_id, coordinator.zone(serial)
        )

    @property
    def zone(self) -> dict[str, Any]:
        """Latest data for this zone."""
        return self.coordinator.zone(self.serial)


class EverlightsCloudBridgeEntity(CoordinatorEntity[EverlightsCloudCoordinator]):
    """An entity that belongs to a bridge."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EverlightsCloudCoordinator,
        entity_description: EntityDescription,
        serial: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self.serial = serial
        self._attr_unique_id = zone_unique_id(serial, entity_description.key)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})

    @property
    def bridge(self) -> dict[str, Any]:
        """Latest data for this bridge."""
        return self.coordinator.bridge(self.serial)

    @property
    def available(self) -> bool:
        """Unavailable while the bridge can't be reached through the cloud."""
        return super().available and bool(self.bridge.get("online"))
