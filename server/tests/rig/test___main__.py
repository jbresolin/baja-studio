"""Tests for the rig's command line (rig/__main__.py): its options and their defaults."""

from pathlib import Path

from baja_studio.rig.__main__ import DEFAULT_SEED, parse_arguments
from baja_studio.rig.layout import default_rig_directory


def test_by_default_the_rig_starts_a_studio_opens_it_and_plays_calm() -> None:
    options = parse_arguments([])
    assert options.studio is True
    assert options.open is True
    assert options.scenario == "calm"
    assert options.seed == DEFAULT_SEED
    assert options.port == 8889
    assert options.rig_dir == default_rig_directory()


def test_each_default_can_be_turned_off() -> None:
    options = parse_arguments(
        ["--no-studio", "--no-open", "--fast-obs", "--reset", "--seed", "7", "--rig-dir", "x"]
    )
    assert (options.studio, options.open, options.fast_obs, options.reset) == (
        False,
        False,
        True,
        True,
    )
    assert options.seed == 7
    assert options.rig_dir == Path("x")
