"""Tests for the XR18 fader law (adapters/xr18_levels.py).

The reference points come from the mixer's own display: the fader scale
printed on an X Air / X32 strip.
"""

import pytest

from baja_studio.adapters.xr18_levels import (
    UNITY_LEVEL,
    clamp_level,
    db_to_level,
    format_db,
    level_to_db,
    nudge_level,
)


@pytest.mark.parametrize(
    ("level", "db"),
    [(1.0, 10.0), (0.75, 0.0), (0.5, -10.0), (0.25, -30.0), (0.0625, -60.0), (0.0, -90.0)],
)
def test_the_segment_corners_match_the_mixer_scale(level: float, db: float) -> None:
    assert level_to_db(level) == pytest.approx(db)
    assert db_to_level(db) == pytest.approx(level)


def test_mid_segment_values_are_linear_in_db() -> None:
    # Halfway between 0.5 (-10 dB) and 0.75 (0 dB) is -5 dB.
    assert level_to_db(0.625) == pytest.approx(-5.0)
    assert db_to_level(-20.0) == pytest.approx(0.375)


@pytest.mark.parametrize("level", [0.01, 0.1, 0.33, 0.6, 0.9])
def test_level_to_db_and_back_is_the_same_level(level: float) -> None:
    assert db_to_level(level_to_db(level)) == pytest.approx(level)


def test_db_is_shown_like_the_2026_tool() -> None:
    assert format_db(0.75) == "0.0 dB"
    assert format_db(0.5) == "-10.0 dB"
    assert format_db(1.0) == "10.0 dB"
    assert format_db(0.0) == "-inf"


def test_values_outside_the_range_are_clamped() -> None:
    assert clamp_level(1.5) == 1.0
    assert clamp_level(-0.1) == 0.0
    assert clamp_level(float("nan")) == 0.0
    assert db_to_level(25.0) == 1.0
    assert db_to_level(-200.0) == 0.0


def test_a_nudge_moves_by_decibels_and_stops_at_plus_ten() -> None:
    assert level_to_db(nudge_level(0.75, 3.0)) == pytest.approx(3.0)
    assert nudge_level(0.98, 3.0) == 1.0


def test_a_nudge_of_an_unknown_level_starts_from_unity_as_in_2026() -> None:
    assert nudge_level(None, -3.0) == pytest.approx(db_to_level(-3.0))
    assert nudge_level(None, 0.0) == UNITY_LEVEL


def test_a_nudge_up_from_off_starts_at_the_bottom() -> None:
    # Off counts as -90 dB: +3 is -87 dB, nearly silent, not a jump to unity.
    assert level_to_db(nudge_level(0.0, 3.0)) == pytest.approx(-87.0)
