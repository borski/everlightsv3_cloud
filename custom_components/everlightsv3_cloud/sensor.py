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
from .entity import EverlightsCloudEntity

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
