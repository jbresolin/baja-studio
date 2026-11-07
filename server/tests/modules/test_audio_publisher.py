"""Tests for audio's state publishing (modules/audio/publisher.py)."""

from typing import Any

from baja_studio.audio.mixer_cache import MixerCache
from baja_studio.modules.audio.publisher import AudioPublisher, channel_view, initial_audio_state


def test_unknown_values_are_null_not_guessed() -> None:
    state = initial_audio_state()

    assert state["channels"]["1"] == {
        "fader": None,
        "db": None,
        "muted": None,
        "name": None,
        "pan": None,
        "sends": dict.fromkeys(["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"]),
    }
    # Buses and the main mix have no sends or pan.
    assert set(state["channels"]["lr"]) == {"fader", "db", "muted", "name"}


def test_a_channel_shows_its_level_in_db() -> None:
    cache = MixerCache()
    cache.faders["2"] = 0.5
    cache.mutes["2"] = True

    view = channel_view(cache, "2")

    assert (view["fader"], view["db"], view["muted"]) == (0.5, "-10.0 dB", True)


def test_repeats_are_not_published_and_sections_merge() -> None:
    published: list[tuple[str, Any]] = []
    publisher = AudioPublisher(lambda path, value: published.append((path, value)))

    publisher.publish("links", {"1": {"bus1": "linked"}})
    publisher.publish("links", {"1": {"bus1": "linked"}})
    publisher.midi(status="ok")
    publisher.midi(selected_bus="bus2")

    assert published[0] == ("links", {"1": {"bus1": "linked"}})
    assert len([p for p in published if p[0] == "links"]) == 1
    assert publisher.current("midi")["status"] == "ok"
    assert publisher.current("midi")["selected_bus"] == "bus2"
