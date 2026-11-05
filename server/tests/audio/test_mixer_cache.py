"""Tests for the cache of mixer values (audio/mixer_cache.py)."""

from baja_studio.adapters.xr18_addresses import ParsedAddress
from baja_studio.audio.mixer_cache import CacheChange, MixerCache


def test_a_new_value_is_a_change_and_the_same_value_again_is_not() -> None:
    cache = MixerCache()
    fader = ParsedAddress("fader", "1")

    assert cache.apply(fader, 0.5) == CacheChange("fader", "1", None, None, 0.5)
    assert cache.apply(fader, 0.5) is None
    assert cache.faders == {"1": 0.5}


def test_mix_on_is_turned_into_muted() -> None:
    # On the XR18, mix/on = 0 means the strip is muted.
    cache = MixerCache()

    cache.apply(ParsedAddress("mute", "lr"), 0)

    assert cache.mutes == {"lr": True}


def test_sends_names_and_pans_are_kept() -> None:
    cache = MixerCache()
    cache.apply(ParsedAddress("send", "aux", "bus2"), 0.25)
    cache.apply(ParsedAddress("name", "3"), "GUEST")
    cache.apply(ParsedAddress("pan", "3"), 0.4)

    assert cache.level("aux", "bus2") == 0.25
    assert cache.names == {"3": "GUEST"}
    assert cache.pans == {"3": 0.4}


def test_a_value_of_the_wrong_type_is_ignored() -> None:
    cache = MixerCache()

    assert cache.apply(ParsedAddress("fader", "1"), "loud") is None
    assert cache.faders == {}


def test_levels_by_point_and_clearing() -> None:
    cache = MixerCache()
    cache.set_level("5", "main", 0.6)
    cache.set_level("5", "bus1", 0.3)

    assert cache.level("5", "main") == 0.6
    assert cache.level("5", "bus1") == 0.3
    cache.clear()
    assert cache.level("5", "main") is None
