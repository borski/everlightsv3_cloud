"""Base entity for Everlights v3 Cloud."""
from __future__ import annotations

from typing import Any

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


def zone_device_info(zone: dict[str, Any]) -> DeviceInfo:
    """Device info for a zone (one device per light controller)."""
    hardware = zone.get("hardwareVersion")
    return DeviceInfo(
        identifiers={(DOMAIN, zone["serial"])},
        name=zone.get("alias") or zone["serial"],
        manufacturer="EverLights",
        model=zone.get("model") or "Controller",
        sw_version=zone.get("firmwareVersion"),
        hw_version=str(hardware) if hardware is not None else None,
        via_device=(DOMAIN, zone["bridgeSerial"]),
    )


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
        self._attr_device_info = zone_device_info(coordinator.zone(serial))

    @property
    def zone(self) -> dict[str, Any]:
        """Latest data for this zone."""
        return self.coordinator.zone(self.serial)
