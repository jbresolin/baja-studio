"""Tests for the MIDI worker's own logic (modules/audio/midi_worker.py).

Its behaviour with the device (port loss, crashes, LEDs) is covered in
test_audio_surface.py through the running module.
"""

from baja_studio.adapters.mvave_map import ButtonPressed, FaderMoved, KnobTurned, MvaveEvent
from baja_studio.modules.audio.midi_worker import _coalesce_faders


def test_a_fader_sweep_is_reduced_to_its_latest_position_per_strip() -> None:
    # A quick sweep sends dozens of positions; only the last of each strip
    # matters, and other controls keep their place in the order.
    events: list[MvaveEvent] = [
        FaderMoved(1, 0.1),
        FaderMoved(2, 0.5),
        ButtonPressed(3, "mute"),
        FaderMoved(1, 0.2),
        KnobTurned(4, 1),
        FaderMoved(1, 0.3),
    ]

    assert _coalesce_faders(events) == [
        FaderMoved(2, 0.5),
        ButtonPressed(3, "mute"),
        KnobTurned(4, 1),
        FaderMoved(1, 0.3),
    ]
