"""Bridge connectivity for Everlights v3 Cloud."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EverlightsCloudConfigEntry
from .entity import EverlightsCloudBridgeEntity

ENTITY_DESCRIPTION = BinarySensorEntityDescription(
    key="online",
    name="Cloud connection",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EverlightsCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one connectivity sensor per bridge."""
    coordinator = entry.runtime_data
    async_add_entities(
        EverlightsCloudConnection(coordinator, ENTITY_DESCRIPTION, serial)
        for serial in coordinator.data["bridges"]
    )


class EverlightsCloudConnection(EverlightsCloudBridgeEntity, BinarySensorEntity):
    """On while the bridge answers through the Everlights cloud."""

    @property
    def available(self) -> bool:
        """Stays available so an outage shows as off, not unknown."""
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        """Return True when the last poll reached the bridge."""
        return self.coordinator.last_update_success and bool(self.bridge.get("online"))
