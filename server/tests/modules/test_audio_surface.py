"""Tests for the M-VAVE as a control surface (modules/audio/surface.py, midi_worker.py).

`audio.press(...)` makes the fake M-VAVE send what the real one sends when
an operator touches it (helpers from fakes/fake_midi.py), and lets the MIDI
worker handle it. `audio.sent_midi()` is what the studio sent back to the
device: LEDs (Note On, velocity 127 or 0) and fader positions (pitch bend).

The 2026 default mapping is in use: strips 1-8 are HOST 1, HOST 2, GUEST 1,
GUEST 2, SHOTGUN (9), PC AUDIO (aux), PROD 1 (5), PROD 2 (6); M mutes, S
closes the strip on the main mix, R on PRODUCTION (bus5), □ on IEM HOST 1.
"""

import pytest

from baja_studio.adapters.mvave_map import fader_message, led_message
from baja_studio.adapters.xr18_addresses import fader_address, mute_address, send_address
from baja_studio.fakes.fake_midi import bus_select, fader, knob, press, release
from tests.modules.audio_studio import AudioStudio


@pytest.fixture
def ready(audio: AudioStudio) -> AudioStudio:
    """Mixer connected, the M-VAVE open, and what was sent so far forgotten."""
    audio.connect_mixer()
    audio.pass_time(0.2)
    audio.sent_midi()
    return audio


def test_a_mute_button_mutes_its_strips_channel_and_lights_its_led(ready: AudioStudio) -> None:
    ready.press(press(3, "mute"))
    ready.settle()

    assert ready.mixer_value(mute_address("3")) == 0
    assert led_message(3, "mute", True) in ready.sent_midi()
    assert ready.state("last_source.3:mute") == "device:mvave"
    assert ("device:mvave", "set_mute", "muted=True") in ready.log()


def test_after_a_release_the_led_is_sent_again_because_the_device_cleared_it(
    ready: AudioStudio,
) -> None:
    ready.press(press(1, "mute"))
    ready.sent_midi()

    ready.press(release(1, "mute"))

    assert ready.sent_midi() == [led_message(1, "mute", True)]


def test_a_mute_made_on_the_page_lights_the_m_vave_led_too(ready: AudioStudio) -> None:
    ready.command("set_mute", channel="2", muted=True)
    ready.pass_time(0.1)

    assert led_message(2, "mute", True) in ready.sent_midi()


def test_s_r_and_square_buttons_close_and_reopen_their_rows_send(ready: AudioStudio) -> None:
    # Strip 7 is PROD 1 (channel 5). R closes its send to PRODUCTION (bus5).
    ready.change_on_mixer(send_address("5", "bus5"), 0.5)
    ready.soon(lambda: ready.state("channels.5")["sends"]["bus5"] == 0.5)

    ready.press(press(7, "rec"))
    ready.settle()
    assert ready.mixer_value(send_address("5", "bus5")) == 0.0
    assert led_message(7, "rec", True) in ready.sent_midi()

    ready.press(press(7, "rec"))
    ready.settle()
    assert ready.mixer_value(send_address("5", "bus5")) == 0.5
    assert led_message(7, "rec", False) in ready.sent_midi()


def test_s_closes_the_strip_on_the_main_mix(ready: AudioStudio) -> None:
    ready.press(press(1, "solo"))
    ready.settle()

    assert ready.mixer_value(fader_address("1")) == 0.0
    assert ready.state("stored_levels") == {"1:main": 0.75}


def test_a_button_mapped_to_a_macro_runs_it_and_shows_its_state(ready: AudioStudio) -> None:
    buttons = ready.module.settings().midi.model_dump()["buttons"]
    buttons["2_sel"] = {"type": "macro", "macro": "host2-talk-prod"}
    midi = {**ready.module.settings().midi.model_dump(), "buttons": buttons}
    ready.configure(midi=midi)
    ready.sent_midi()

    ready.press(press(2, "sel"))
    ready.settle()

    assert ready.state("macros.host2-talk-prod")["active"] is True
    assert led_message(2, "sel", True) in ready.sent_midi()


def test_a_fader_sets_its_strips_main_fader_once_it_has_picked_up_the_level(
    ready: AudioStudio,
) -> None:
    # Channel 1 is at 0.75 on the mixer. Pickup mode: the fader does nothing
    # until it reaches 0.75, so the level cannot jump.
    ready.press(fader(1, 0.2))
    assert ready.state("channels.1")["fader"] == 0.75
    assert ready.state("midi")["pickup"] == {"1": 0.75}

    ready.press(fader(1, 0.8))  # crossed 0.75 on the way: picked up
    ready.settle()

    assert ready.mixer_value(fader_address("1")) == pytest.approx(0.8, abs=0.001)
    ready.press(fader(1, 0.5))
    assert ready.state("channels.1")["fader"] == pytest.approx(0.5, abs=0.001)


def test_a_change_from_elsewhere_drops_the_pickup_again(ready: AudioStudio) -> None:
    ready.press(fader(1, 0.75))  # picked up at once
    ready.command("set_fader", channel="1", level=0.25)
    ready.settle()

    ready.press(fader(1, 0.7))

    assert ready.state("channels.1")["fader"] == 0.25


def test_jump_mode_applies_every_move_as_in_2026(ready: AudioStudio) -> None:
    midi = {**ready.module.settings().midi.model_dump(), "fader_takeover": "jump"}
    ready.configure(midi=midi)

    ready.press(fader(1, 0.2))
    ready.settle()

    assert ready.mixer_value(fader_address("1")) == pytest.approx(0.2, abs=0.001)


def test_fader_positions_from_elsewhere_are_sent_to_the_device(ready: AudioStudio) -> None:
    ready.command("set_fader", channel="2", level=0.5)
    ready.pass_time(0.1)

    assert fader_message(2, 0.5) in ready.sent_midi()


def test_knobs_turn_the_selected_buss_send_and_do_nothing_without_one(ready: AudioStudio) -> None:
    # Strip 3 is GUEST 1 (channel 3). No bus selected: ignored, as in 2026.
    ready.press(knob(3, 2))
    assert ready.state("last_source").get("3:bus2") is None

    ready.press(bus_select("bus2"))
    assert ready.state("midi.selected_bus") == "bus2"
    ready.press(knob(3, 2))  # two detents of 1.5 dB
    ready.settle()

    assert ready.state("last_source.3:bus2") == "device:mvave"
    # A direct send change pauses its link (3 → bus2 was linked).
    assert ready.state("links")["3"]["bus2"] == "paused"


def test_pressing_the_selected_bus_again_clears_it(ready: AudioStudio) -> None:
    ready.press(bus_select("bus4"))
    ready.press(bus_select("bus4"))

    assert ready.state("midi.selected_bus") is None


def test_midi_learn_captures_the_next_control_instead_of_acting(ready: AudioStudio) -> None:
    ready.command("midi_learn", action="arm", source="web:bob")
    assert ready.state("midi.learn")["armed"] is True

    ready.press(press(4, "sel"))

    learn = ready.state("midi.learn")
    assert learn["armed"] is False
    assert learn["by"] == "web:bob"
    assert learn["captured"]["control"] == "button:4_sel"
    assert ready.state("stored_levels") == {}  # the button did not act


def test_unplugging_the_m_vave_goes_offline_and_replugging_resends_every_led(
    ready: AudioStudio,
) -> None:
    ready.command("set_mute", channel="1", muted=True)
    ready.pass_time(0.1)
    host = ready.midi_hosts[-1]

    ready.midi_backend.plugged = False
    host.child.check_port()  # the child's watcher does this every second
    ready.soon(lambda: ready.state("midi.status") == "offline")

    ready.midi_backend.plugged = True
    host.child.check_port()
    ready.soon(lambda: ready.state("midi.status") == "ok")
    ready.pass_time(0.1)

    sent = ready.sent_midi()
    assert led_message(1, "mute", True) in sent
    # Every one of the 32 button LEDs was sent again, lit or not.
    assert len({m[1] for m in sent if m[0] == 0x90}) == 32


def test_a_crashed_midi_child_is_replaced(ready: AudioStudio) -> None:
    ready.midi_hosts[-1].crash()

    ready.soon(lambda: len(ready.midi_hosts) == 2, within_seconds=5.0)
    ready.soon(lambda: ready.state("midi.status") == "ok")


def test_turning_midi_off_stops_the_child(ready: AudioStudio) -> None:
    midi = {**ready.module.settings().midi.model_dump(), "enabled": False}
    ready.configure(midi=midi)

    ready.soon(lambda: ready.state("midi.status") == "disabled")
    assert not ready.midi_hosts[-1].is_running
