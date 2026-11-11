"""Diagnostic sensors for Everlights v3 Cloud."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EverlightsCloudConfigEntry, parse_timestamp
from .entity import EverlightsCloudBridgeEntity, EverlightsCloudEntity

# Keys match the local `everlights` integration so its sensors can be adopted.
ENTITY_DESCRIPTIONS = (
    SensorEntityDescription(
        key="rssi",
        name="Signal Strength",
        icon="mdi:signal-variant",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="snr",
        name="Signal to Noise",
        icon="mdi:sine-wave",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="lastRequestDate",
        name="Last Request Date",
        icon="mdi:timeline-clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="lastResponseDate",
        name="Last Response Date",
        icon="mdi:timeline-clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="rebootCount",
        name="Reboot Count",
        icon="mdi:restart",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # The bridge reports these as bare numbers and nothing documents the units.
    SensorEntityDescription(
        key="voltage",
        name="Voltage Raw",
        icon="mdi:flash-triangle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="temperature",
        name="Temperature Raw",
        icon="mdi:thermometer-lines",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="configuredLength",
        name="Configured Length",
        icon="mdi:ruler",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

BRIDGE_DESCRIPTIONS = (
    SensorEntityDescription(
        key="lastServerCheckin",
        name="Last Check-in",
        icon="mdi:cloud-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="eventCount",
        name="Scheduled Shows",
        icon="mdi:calendar-star",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="clockOffset",
        name="Clock Offset",
        icon="mdi:clock-alert-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="rebootCount",
        name="Reboot Count",
        icon="mdi:restart",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EverlightsCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        EverlightsCloudSensor(coordinator, description, serial)
        for serial in coordinator.data["zones"]
        for description in ENTITY_DESCRIPTIONS
    )
    async_add_entities(
        EverlightsCloudBridgeSensor(coordinator, description, serial)
        for serial in coordinator.data["bridges"]
        for description in BRIDGE_DESCRIPTIONS
    )


class EverlightsCloudSensor(EverlightsCloudEntity, SensorEntity):
    """A zone reading reported by the bridge."""

    @property
    def available(self) -> bool:
        """Readings are only trusted while the zone's bridge is reachable."""
        return super().available and bool(self.zone.get("online"))

    @property
    def native_value(self) -> Any:
        """Return the reading."""
        value = self.zone.get(self.entity_description.key)
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return parse_timestamp(value)
        return value


class EverlightsCloudBridgeSensor(EverlightsCloudBridgeEntity, SensorEntity):
    """A reading about the bridge itself."""

    @property
    def native_value(self) -> Any:
        """Return the reading."""
        value = self.bridge.get(self.entity_description.key)
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return parse_timestamp(value)
        return value
