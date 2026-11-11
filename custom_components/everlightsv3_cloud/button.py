"""Bridge maintenance buttons for Everlights v3 Cloud."""
from __future__ import annotations

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EverlightsCloudConfigEntry
from .entity import EverlightsCloudBridgeEntity

SYNC_CLOCK = ButtonEntityDescription(
    key="sync_clock",
    name="Sync clock",
    icon="mdi:clock-check-outline",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)
REBOOT = ButtonEntityDescription(
    key="reboot",
    name="Restart",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EverlightsCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons. Both start disabled so nothing is pressed by accident."""
    coordinator = entry.runtime_data
    async_add_entities(
        EverlightsCloudButton(coordinator, description, serial)
        for serial in coordinator.data["bridges"]
        for description in (SYNC_CLOCK, REBOOT)
    )


class EverlightsCloudButton(EverlightsCloudBridgeEntity, ButtonEntity):
    """A one-shot bridge command."""

    async def async_press(self) -> None:
        """Send the command once."""
        if self.entity_description.key == REBOOT.key:
            await self.coordinator.async_reboot(self.serial)
        else:
            await self.coordinator.async_sync_clock(self.serial)
