"""Pattern maths: brightness is done by scaling colors."""
import pytest

from custom_components.everlightsv3_cloud.patterns import (
    match_scaled,
    normalize_pattern,
    normalize_sequence,
    scale_pattern,
)


def test_normalize():
    assert normalize_pattern(["ff0000", "#00Ff00", "nope", None]) == ["FF0000", "00FF00"]
    assert normalize_sequence(None) == {"pattern": [], "effects": []}
    assert normalize_sequence({"pattern": ["aa0000"], "effects": [{"effectType": "fade", "value": 9, "x": 1}]}) == {
        "pattern": ["AA0000"], "effects": [{"effectType": "fade", "value": 9}]}


def test_scale():
    assert scale_pattern(["FF8000"], 255) == ["FF8000"]
    assert scale_pattern(["FF8000"], 128) == ["804000"]
    # A lit channel never rounds down to off, and an off channel stays off.
    assert scale_pattern(["FF0100"], 1) == ["010100"]


@pytest.mark.parametrize("brightness", [1, 2, 3, 17, 64, 127, 128, 200, 254, 255])
def test_match_round_trips(brightness):
    base = ["FF0000", "FFFFFF", "FC00FF", "00FF40"]
    assert match_scaled(scale_pattern(base, brightness), base) is not None
    assert scale_pattern(base, match_scaled(scale_pattern(base, brightness), base)) == scale_pattern(base, brightness)


def test_match_rejects_other_patterns():
    assert match_scaled(["FF0000"], ["00FF00"]) is None
    assert match_scaled(["FF0000"], ["FF0000", "FF0000"]) is None
    assert match_scaled([], ["FF0000"]) is None
