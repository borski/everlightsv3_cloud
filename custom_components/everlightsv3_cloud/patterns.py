"""Pure helpers for Everlights patterns (lists of RRGGBB hex strings).

The bridge has no brightness control, so brightness is done by scaling every
colour in a pattern. These helpers scale patterns and recognise them again
when they are read back from the bridge.
"""
from __future__ import annotations

from typing import Any

RGB = tuple[int, int, int]


def hex_to_rgb(value: str) -> RGB:
    """Parse RRGGBB (optionally prefixed with #) into an RGB tuple."""
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Not an RRGGBB colour: {value!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: RGB) -> str:
    """Format an RGB tuple as upper-case RRGGBB."""
    return "".join(f"{max(0, min(255, int(c))):02X}" for c in rgb)


def normalize_pattern(pattern: Any) -> list[str]:
    """Upper-case a pattern and drop anything that is not a colour."""
    out: list[str] = []
    for item in pattern or []:
        try:
            out.append(rgb_to_hex(hex_to_rgb(str(item))))
        except ValueError:
            continue
    return out


def normalize_effects(effects: Any) -> list[dict[str, Any]]:
    """Reduce effects to comparable {effectType, value} dicts."""
    return [
        {"effectType": e.get("effectType"), "value": e.get("value")}
        for e in effects or []
        if isinstance(e, dict)
    ]


def normalize_sequence(sequence: dict[str, Any] | None) -> dict[str, list]:
    """Return a canonical {"pattern", "effects"} form used for comparisons."""
    sequence = sequence or {}
    return {
        "pattern": normalize_pattern(sequence.get("pattern")),
        "effects": normalize_effects(sequence.get("effects")),
    }


def pattern_peak(pattern: list[str]) -> int:
    """Brightest single channel in a pattern (0 to 255)."""
    return max((max(hex_to_rgb(c)) for c in pattern), default=0)


def scale_pattern(pattern: list[str], brightness: int) -> list[str]:
    """Scale every colour by brightness/255. Lit channels never round to zero."""
    brightness = max(1, min(255, int(brightness)))
    if brightness == 255:
        return normalize_pattern(pattern)
    out = []
    for colour in normalize_pattern(pattern):
        out.append(
            rgb_to_hex(
                tuple(max(1, round(c * brightness / 255)) if c else 0 for c in hex_to_rgb(colour))  # type: ignore[arg-type]
            )
        )
    return out


def match_scaled(candidate: list[str], base: list[str]) -> int | None:
    """Return the brightness at which `base` scales to `candidate`, else None."""
    candidate, base = normalize_pattern(candidate), normalize_pattern(base)
    if not base or len(candidate) != len(base):
        return None
    if candidate == base:
        return 255
    base_peak = pattern_peak(base)
    if not base_peak:
        return None
    guess = round(pattern_peak(candidate) * 255 / base_peak)
    for brightness in (guess, guess - 1, guess + 1, guess - 2, guess + 2):
        if 1 <= brightness <= 255 and scale_pattern(base, brightness) == candidate:
            return brightness
    return None
