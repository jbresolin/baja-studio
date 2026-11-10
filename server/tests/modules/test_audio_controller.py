"""Tests for the audio controller's rules (modules/audio/controller.py).

Through the running module and the simulated XR18, as an operator would
see them: links that mirror, pause and unlink; macros that close and open
routes and remember levels; scenes; who changed what.
"""

import pytest

from baja_studio.adapters.xr18_addresses import fader_address, mute_address, send_address
from baja_studio.core.module import CommandError
from tests.modules.audio_studio import AudioStudio


def send_level(audio: AudioStudio, channel: str, bus: str) -> float:
    value = audio.mixer_value(send_address(channel, bus))
    assert isinstance(value, float)
    return value


def test_a_linked_send_follows_its_fader_from_any_source_mixing_station_included(
    audio: AudioStudio,
) -> None:
    # HOST 1 is linked to IEM HOST 1 (bus1) by the 2026 seed. Someone moves
    # HOST 1 in Mixing Station: the IEM send follows.
    audio.connect_mixer()

    audio.change_on_mixer(fader_address("1"), 0.25)

    audio.soon(lambda: send_level(audio, "1", "bus1") == 0.25)


def test_paused_keeps_the_link_but_stops_mirroring_and_unlinked_removes_it(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()
    audio.command("set_link", channel="1", bus="bus1", state="paused")
    audio.command("set_link", channel="1", bus="bus2", state="unlinked")

    audio.command("set_fader", channel="1", level=0.25)
    audio.settle()

    assert send_level(audio, "1", "bus1") != pytest.approx(0.25)
    assert send_level(audio, "1", "bus2") != pytest.approx(0.25)
    assert send_level(audio, "1", "bus3") == pytest.approx(0.25)
    assert audio.state("links")["1"]["bus1"] == "paused"
    assert "bus2" not in audio.state("links")["1"]


def test_relinking_brings_the_send_to_the_fader_at_once(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.command("set_link", channel="1", bus="bus1", state="paused")
    audio.command("set_fader", channel="1", level=0.25)

    audio.command("set_link", channel="1", bus="bus1", state="linked")
    audio.settle()

    assert send_level(audio, "1", "bus1") == pytest.approx(0.25)


def test_adjusting_a_linked_send_directly_pauses_its_link(audio: AudioStudio) -> None:
    # The camera operator (bus6, not a seat's in-ears) wants more of host 1.
    # The tweak must survive host 1's next fader move (in 2026 the D200
    # nudge did not). On a seat's in-ear bus the link stays instead and the
    # tweak becomes an offset (test_audio_seat_mix.py).
    audio.connect_mixer()

    audio.command("nudge", channel="1", bus="bus6", delta_db=3.0, source="device:d200")
    tweaked = audio.state("channels.1")["sends"]["bus6"]
    audio.command("set_fader", channel="1", level=0.5)
    audio.settle()

    assert audio.state("links")["1"]["bus6"] == "paused"
    assert audio.state("channels.1")["sends"]["bus6"] == tweaked


def test_a_send_closed_by_a_macro_stays_closed_when_its_linked_fader_moves(
    audio: AudioStudio,
) -> None:
    # PROD 1 (channel 5) is linked to bus1. "Prod 1 → Hosts" is off, so the
    # talkback route is closed; moving PROD 1's fader must not open it.
    audio.connect_mixer()
    # The route is open (set at the desk, so the link is not paused)...
    audio.change_on_mixer(send_address("5", "bus1"), 0.5)
    audio.soon(lambda: audio.state("channels.5")["sends"]["bus1"] == 0.5)
    # ...and then closed by the M-VAVE's button or the macro.
    assert audio.command("toggle_send", channel="5", bus="bus1")["open"] is False
    audio.settle()
    assert send_level(audio, "5", "bus1") == 0.0

    audio.command("set_fader", channel="5", level=0.6)
    audio.settle()

    assert send_level(audio, "5", "bus1") == 0.0


def test_a_talkback_macro_round_trip_remembers_the_level(audio: AudioStudio) -> None:
    # "Prod 1 → Cam" (bus6, paused link in the seed): the route is open at
    # 0.5. Off closes it and remembers 0.5; on brings exactly 0.5 back.
    macro = "macro-1774582749617"
    audio.connect_mixer()
    audio.command("set_aux", channel="5", bus="bus6", level=0.5)
    audio.settle()
    assert audio.state(f"macros.{macro}")["active"] is True

    assert audio.command("run_macro", macro=macro) == {"macro": macro, "active": False}
    audio.settle()
    assert send_level(audio, "5", "bus6") == 0.0
    assert audio.state("stored_levels") == {"5:bus6": 0.5}

    assert audio.command("run_macro", macro=macro)["active"] is True
    audio.settle()
    assert send_level(audio, "5", "bus6") == 0.5
    assert audio.state("stored_levels") == {}


def test_a_macro_turned_on_in_mixing_station_shows_active(audio: AudioStudio) -> None:
    # Active is worked out from the mixer, not remembered.
    macro = "macro-1774582227201"  # Prod 1 Go Live: channel 5's main fader open
    audio.connect_mixer()
    audio.command("toggle_send", channel="5", bus="main")
    # Our own write is protected for 300 ms; Mixing Station acts later.
    audio.settle()
    audio.pass_time(0.5)
    assert audio.state(f"macros.{macro}")["active"] is False

    audio.change_on_mixer(fader_address("5"), 0.5)

    audio.soon(lambda: audio.state(f"macros.{macro}")["active"] is True)


def test_mute_toggles_and_sets(audio: AudioStudio) -> None:
    audio.connect_mixer()

    assert audio.command("set_mute", channel="2")["muted"] is True
    assert audio.command("set_mute", channel="2")["muted"] is False
    assert audio.command("set_mute", channel="2", muted=True)["muted"] is True
    audio.settle()
    assert audio.mixer_value(mute_address("2")) == 0
    assert audio.state("last_source.2:mute") == "web:alice"


def test_a_configured_scene_loads_its_snapshot_and_runs_its_macro(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.mixer.snapshots[7] = {**audio.mixer.values, fader_address("9"): 0.25}
    audio.configure(
        scenes=[{"index": 2, "label": "Race", "snapshot": 7, "macro": "prod1-talk-hosts"}]
    )

    audio.command("recall_scene", index=2)

    audio.soon(lambda: audio.state("channels.9")["fader"] == 0.25)
    assert audio.state("macros.prod1-talk-hosts")["active"] is True


def test_an_unconfigured_scene_number_loads_that_snapshot_as_in_2026(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.mixer.snapshots[5] = {**audio.mixer.values, fader_address("3"): 0.25}

    audio.command("recall_scene", index=5)

    audio.soon(lambda: audio.state("channels.3")["fader"] == 0.25)
    with pytest.raises(CommandError, match="1-64"):
        audio.command("recall_scene", index=65)


def test_an_unknown_macro_is_refused_with_where_to_look(audio: AudioStudio) -> None:
    audio.connect_mixer()

    with pytest.raises(CommandError, match="no macro 'ghost'"):
        audio.command("run_macro", macro="ghost")
