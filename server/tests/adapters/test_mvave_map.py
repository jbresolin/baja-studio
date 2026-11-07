"""Tests for the M-VAVE SMC-Mixer message map (adapters/mvave_map.py).

Every control on the device is checked: the bytes it sends (as sniffed in
2026) must decode to the right strip and control, and the bytes we send
must light the right LED.
"""

import pytest

from baja_studio.adapters.mvave_map import (
    BusSelectPressed,
    ButtonPressed,
    ButtonReleased,
    FaderMoved,
    KnobTurned,
    UnknownMessage,
    button_note,
    decode,
    fader_message,
    led_message,
)

ROW_OFFSETS = {"rec": 0x00, "solo": 0x08, "mute": 0x10, "sel": 0x18}


@pytest.mark.parametrize("strip", range(1, 9))
@pytest.mark.parametrize("row", ["rec", "solo", "mute", "sel"])
def test_every_strip_button_press_and_release_decodes(strip: int, row: str) -> None:
    # R = base, S = base + 8, M = base + 0x10, □ = base + 0x18; base = strip - 1.
    note = strip - 1 + ROW_OFFSETS[row]

    assert decode([0x90, note, 127]) == ButtonPressed(strip, row)
    assert decode([0x80, note, 0]) == ButtonReleased(strip, row)
    # Some devices send "Note On, velocity 0" for a release.
    assert decode([0x90, note, 0]) == ButtonReleased(strip, row)
    assert button_note(strip, row) == note


@pytest.mark.parametrize(
    ("note", "bus"),
    [
        (0x2E, "bus1"),
        (0x2F, "bus2"),
        (0x60, "bus3"),
        (0x61, "bus4"),
        (0x62, "bus5"),
        (0x63, "bus6"),
    ],
)
def test_the_bus_select_buttons_decode(note: int, bus: str) -> None:
    assert decode([0x90, note, 127]) == BusSelectPressed(bus)


@pytest.mark.parametrize("strip", range(1, 9))
def test_every_knob_is_relative(strip: int) -> None:
    controller = 0x10 + strip - 1

    assert decode([0xB0, controller, 0x01]) == KnobTurned(strip, 1)
    assert decode([0xB0, controller, 0x41]) == KnobTurned(strip, -1)
    # A fast turn arrives as several steps in one message.
    assert decode([0xB0, controller, 0x03]) == KnobTurned(strip, 3)
    assert decode([0xB0, controller, 0x44]) == KnobTurned(strip, -4)


@pytest.mark.parametrize("strip", range(1, 9))
def test_every_fader_is_pitch_bend_on_its_own_channel(strip: int) -> None:
    channel_status = 0xE0 + strip - 1

    assert decode([channel_status, 0x00, 0x00]) == FaderMoved(strip, 0.0)
    assert decode([channel_status, 0x7F, 0x7F]) == FaderMoved(strip, 1.0)
    # 8192 (0x2000) is the middle: lsb 0x00, msb 0x40.
    middle = decode([channel_status, 0x00, 0x40])
    assert isinstance(middle, FaderMoved)
    assert middle.level == pytest.approx(0.5, abs=0.001)


@pytest.mark.parametrize(
    "raw",
    [[0x90, 0x2A, 127], [0xB0, 0x20, 1], [0xE8, 0, 0], [0xF8], [0x90, 0x10]],
)
def test_anything_else_is_unknown_and_named_for_midi_learn(raw: list[int]) -> None:
    event = decode(raw)

    assert isinstance(event, UnknownMessage)
    assert event.control.startswith("raw:")


def test_controls_have_the_names_used_by_mappings_and_midi_learn() -> None:
    assert decode([0x90, 0x0A, 127]).control == "button:3_solo"
    assert decode([0xE1, 0, 0]).control == "fader:2"
    assert decode([0xB0, 0x17, 1]).control == "knob:8"
    assert decode([0x90, 0x2F, 127]).control == "bus:bus2"


def test_leds_are_lit_and_cleared_with_note_on() -> None:
    assert led_message(1, "mute", True) == [0x90, 0x10, 127]
    assert led_message(8, "sel", False) == [0x90, 0x1F, 0]


def test_fader_positions_go_back_as_pitch_bend() -> None:
    assert fader_message(1, 1.0) == [0xE0, 0x7F, 0x7F]
    assert fader_message(4, 0.0) == [0xE3, 0x00, 0x00]
    assert fader_message(2, 0.5) == [0xE1, 0x00, 0x40]
