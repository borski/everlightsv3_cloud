"""Light platform for Everlights v3 Cloud."""
from __future__ import annotations

from typing import Any

import homeassistant.helpers.config_validation as cv
import homeassistant.util.color as color_util
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_COLOR,
    EFFECT_TYPES,
    OFF_SEQUENCE,
    SERVICE_CLEAR,
    SERVICE_DELETE_SEQUENCE,
    SERVICE_SAVE_SEQUENCE,
    SERVICE_UPDATE_SEQUENCE,
)
from .coordinator import EverlightsCloudConfigEntry, EverlightsCloudCoordinator
from .entity import EverlightsCloudEntity
from .patterns import (
    hex_to_rgb,
    match_scaled,
    normalize_effects,
    normalize_pattern,
    pattern_peak,
    rgb_to_hex,
    scale_pattern,
)

# Key "pattern" keeps the unique ID identical to the local integration's.
ENTITY_DESCRIPTION = LightEntityDescription(
    key="pattern", name="Everlights", icon="mdi:led-on"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EverlightsCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one light per zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        EverlightsCloudLight(coordinator, ENTITY_DESCRIPTION, serial)
        for serial in coordinator.data["zones"]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_CLEAR, None, "async_clear")
    platform.async_register_entity_service(
        SERVICE_SAVE_SEQUENCE,
        {
            vol.Required("name"): cv.string,
            vol.Optional("colors"): vol.All(cv.ensure_list, [_hex_colour], vol.Length(min=1)),
            vol.Optional("effects"): {vol.In(EFFECT_TYPES): vol.All(vol.Coerce(int), vol.Range(0, 255))},
        },
        "async_save_sequence",
    )
    platform.async_register_entity_service(
        SERVICE_UPDATE_SEQUENCE,
        {
            vol.Required("name"): cv.string,
            vol.Optional("new_name"): cv.string,
            vol.Optional("colors"): vol.All(cv.ensure_list, [_hex_colour], vol.Length(min=1)),
            vol.Optional("effects"): {vol.In(EFFECT_TYPES): vol.All(vol.Coerce(int), vol.Range(0, 255))},
        },
        "async_update_sequence",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_SEQUENCE, {vol.Required("name"): cv.string}, "async_delete_sequence"
    )


def _hex_colour(value: Any) -> str:
    try:
        return rgb_to_hex(hex_to_rgb(str(value)))
    except ValueError as err:
        raise vol.Invalid(f"{value!r} is not an RRGGBB colour") from err


class EverlightsCloudLight(EverlightsCloudEntity, LightEntity):
    """One Everlights zone.

    The bridge only understands a list of colours plus effects. There is no
    brightness, so brightness is done by dimming the colours themselves.
    `_base` is the full-brightness pattern the light is showing.
    """

    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(
        self,
        coordinator: EverlightsCloudCoordinator,
        entity_description: LightEntityDescription,
        serial: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entity_description, serial)
        self._base: list[str] = []
        self._base_effects: list[dict[str, Any]] = []
        self._attr_effect: str | None = None
        self._attr_brightness: int | None = None
        self._attr_hs_color: tuple[float, float] | None = None
        self._attr_is_on = False
        # What to show for a bare "turn on" after the light was off.
        self._last: tuple[list[str], list[dict[str, Any]], str | None, int] | None = None
        self._sync_from_coordinator()

    # ------------------------------------------------------------ library

    @property
    def _library(self) -> dict[str, dict[str, Any]]:
        return {
            s["alias"]: s
            for s in self.coordinator.sequences
            if s.get("alias") and normalize_pattern(s.get("pattern"))
        }

    @property
    def effect_list(self) -> list[str]:
        """Sequences stored on the bridge, plus "off" for a plain colour."""
        return [EFFECT_OFF, *sorted(self._library)]

    # -------------------------------------------------------------- state

    @property
    def available(self) -> bool:
        """Unavailable when the cloud, the bridge, or the zone stops answering."""
        return super().available and self.coordinator.zone_available(self.serial)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_coordinator()
        super()._handle_coordinator_update()

    @callback
    def _sync_from_coordinator(self) -> None:
        """Work out colour, brightness, and effect from what the bridge reports."""
        zone = self.zone
        pattern = normalize_pattern(zone.get("pattern"))
        effects = normalize_effects(zone.get("effects"))
        if not pattern:
            self._attr_is_on = False
            return
        self._attr_is_on = True
        if (
            self._base
            and effects == self._base_effects
            and self._attr_brightness
            and scale_pattern(self._base, self._attr_brightness) == pattern
        ):
            return  # Still showing what we last understood.

        # Changed from somewhere else (the app, a schedule) or first load.
        for alias in sorted(self._library):
            sequence = self._library[alias]
            if normalize_effects(sequence.get("effects")) != effects:
                continue
            brightness = match_scaled(pattern, sequence["pattern"])
            if brightness is not None:
                self._set_shown(normalize_pattern(sequence["pattern"]), effects, alias, brightness)
                return
        peak = pattern_peak(pattern) or 255
        base = [
            rgb_to_hex(tuple(min(255, round(c * 255 / peak)) for c in hex_to_rgb(colour)))  # type: ignore[arg-type]
            for colour in pattern
        ]
        self._set_shown(base, effects, None, peak)

    @callback
    def _set_shown(
        self, base: list[str], effects: list[dict[str, Any]], effect: str | None, brightness: int
    ) -> None:
        self._base = base
        self._base_effects = effects
        self._attr_brightness = brightness
        if effect is None:
            # A custom pattern with effects that is not in the library.
            self._attr_effect = None if effects else EFFECT_OFF
        else:
            self._attr_effect = effect
        self._attr_hs_color = color_util.color_RGB_to_hs(*hex_to_rgb(base[0]))
        self._last = (base, effects, effect, brightness)

    # ----------------------------------------------------------- commands

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, or change colour, brightness, or effect."""
        effect = kwargs.get(ATTR_EFFECT)
        hs_color = kwargs.get(ATTR_HS_COLOR)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if self._attr_is_on and self._base:
            current = (self._base, self._base_effects, self._attr_effect, self._attr_brightness)
        else:
            current = self._last or ([DEFAULT_COLOR], [], None, 255)
        base, effects, name, current_brightness = current
        if name == EFFECT_OFF:
            name = None

        if effect is not None and effect != EFFECT_OFF:
            sequence = self._library.get(effect)
            if sequence is None:
                raise ServiceValidationError(
                    f"Unknown Everlights effect '{effect}'. Pick one from the effect list"
                )
            base = normalize_pattern(sequence["pattern"])
            effects = normalize_effects(sequence.get("effects"))
            name = effect
        elif hs_color is not None or effect == EFFECT_OFF:
            # A colour (or effect "off") means a plain solid colour.
            if hs_color is None:
                hs_color = self._attr_hs_color or (0.0, 0.0)
            base = [rgb_to_hex(color_util.color_hs_to_RGB(*hs_color))]
            effects = []
            name = None

        brightness = max(1, min(255, int(brightness or current_brightness or 255)))
        await self.coordinator.async_send_sequence(
            self.serial, {"pattern": scale_pattern(base, brightness), "effects": effects}
        )
        # Only reached once the read-back confirmed the change.
        self._set_shown(base, effects, name, brightness)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **_: Any) -> None:
        """Turn off (an empty pattern)."""
        await self.coordinator.async_send_sequence(self.serial, OFF_SEQUENCE)
        self._attr_is_on = False
        self.async_write_ha_state()

    # ----------------------------------------------------------- services

    async def async_clear(self) -> None:
        """Drop what was sent to this zone and let the bridge's schedule take over."""
        await self.coordinator.async_clear_zone(self.serial)

    async def async_save_sequence(
        self, name: str, colors: list[str] | None = None, effects: dict[str, int] | None = None
    ) -> None:
        """Save colours as a sequence on the bridge. Defaults to what this light shows now."""
        if colors is None:
            if not (self._attr_is_on and self._base):
                raise ServiceValidationError(
                    "The light is off. Turn it on or pass colours to save"
                )
            pattern = list(self._base)
            effect_list = self._base_effects if effects is None else None
        else:
            pattern, effect_list = colors, None
        if effect_list is None:
            effect_list = [{"effectType": k, "value": v} for k, v in (effects or {}).items()]
        await self.coordinator.async_save_sequence(self.serial, name, pattern, effect_list)

    async def async_update_sequence(
        self,
        name: str,
        new_name: str | None = None,
        colors: list[str] | None = None,
        effects: dict[str, int] | None = None,
    ) -> None:
        """Change the colours, effects, or name of one of your own saved sequences."""
        if new_name is None and colors is None and effects is None:
            raise ServiceValidationError("Nothing to change. Pass a new name, colours, or effects")
        effect_list = (
            None if effects is None else [{"effectType": k, "value": v} for k, v in effects.items()]
        )
        await self.coordinator.async_update_sequence(
            self.serial, name, colors, effect_list, new_name
        )

    async def async_delete_sequence(self, name: str) -> None:
        """Delete one of your own saved sequences from the bridge."""
        await self.coordinator.async_delete_sequence(self.serial, name)
