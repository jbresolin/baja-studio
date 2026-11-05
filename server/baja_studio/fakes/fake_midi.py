"""A fake M-VAVE SMC-Mixer: an in-memory MIDI port for the MIDI child, and its scripts.

`FakeMidiBackend` stands in for python-rtmidi inside the child (in tests
that call `MidiChild` directly, or in the real child process when started
with `BAJA_STUDIO_MIDI_BACKEND=fake`). Through the child it answers:

- `fake_inject {messages}`: the device sends these (a person at the surface).
- `fake_unplug` / `fake_replug`: the USB cable.
- `fake_sent`: what the studio sent the device (LEDs, fader positions), then forgets it.

The helpers below (`fader`, `press`, `release`, `knob`, `bus_select`) build
the raw messages the real device sends, so scripts read like what an
operator does. Running this file demonstrates the whole chain:

    python -m baja_studio.fakes.fake_midi

starts the real MIDI child against the fake, opens the "SMC-Mixer", plays a
short script and prints the events the service would receive.
"""

import json
import os
import subprocess
import sys
from typing import Any

from baja_studio.adapters.mvave_map import (
    BUS_SELECT_NOTES,
    CONTROL_CHANGE,
    KNOB_FIRST_CC,
    NOTE_OFF,
    NOTE_ON,
    PITCH_BEND,
    PITCH_BEND_MAX,
    button_note,
)
from baja_studio.childprocs.midi_child import BACKEND_ENVIRONMENT_VARIABLE, MessageCallback

DEVICE_NAME = "SMC-Mixer"


def fader(strip: int, level: float) -> list[int]:
    """The operator moves strip `strip`'s fader to `level` (0..1)."""
    value = round(min(1.0, max(0.0, level)) * PITCH_BEND_MAX)
    return [PITCH_BEND | (strip - 1), value & 0x7F, value >> 7]


def press(strip: int, row: str) -> list[int]:
    """A strip button goes down; `row` is rec, solo, mute or sel."""
    return [NOTE_ON, button_note(strip, row), 127]


def release(strip: int, row: str) -> list[int]:
    """A strip button comes up (the device clears its LED now)."""
    return [NOTE_OFF, button_note(strip, row), 0]


def knob(strip: int, steps: int) -> list[int]:
    """A knob turns `steps` detents (negative: down), as one relative CC."""
    value = steps if steps > 0 else 0x40 - steps
    return [CONTROL_CHANGE, KNOB_FIRST_CC + strip - 1, value]


def bus_select(bus: str) -> list[int]:
    """A bottom bus-select button goes down."""
    note = next(note for note, name in BUS_SELECT_NOTES.items() if name == bus)
    return [NOTE_ON, note, 127]


class FakeMidiBackend:
    """One fake device, plugged in at the start."""

    def __init__(self, device_name: str = DEVICE_NAME) -> None:
        """Plugged in, not opened."""
        self.device_name = device_name
        self.plugged = True
        self.sent: list[list[int]] = []
        self._callback: MessageCallback | None = None

    def input_names(self) -> list[str]:
        """The device, while plugged in."""
        return [self.device_name] if self.plugged else []

    def output_names(self) -> list[str]:
        """The device, while plugged in."""
        return self.input_names()

    def open(self, input_name: str, output_name: str, on_message: MessageCallback) -> None:
        """Open; fails like a driver would when unplugged."""
        if not self.plugged:
            raise OSError("device not present")
        self._callback = on_message

    def send(self, message: list[int]) -> None:
        """Record what the studio sent."""
        if not self.plugged or self._callback is None:
            raise OSError("device not present")
        self.sent.append(list(message))

    def close(self) -> None:
        """Forget the callback."""
        self._callback = None

    def inject(self, messages: list[list[int]]) -> int:
        """The device sends `messages` (only heard while open)."""
        if self._callback is None:
            return 0
        for message in messages:
            self._callback(list(message))
        return len(messages)

    def handle_fake(self, method: str, params: dict[str, Any]) -> Any:
        """The `fake_*` requests, forwarded by the MIDI child."""
        if method == "fake_inject":
            return self.inject(params.get("messages", []))
        if method == "fake_unplug":
            self.plugged = False
            return None
        if method == "fake_replug":
            self.plugged = True
            return None
        if method == "fake_sent":
            sent, self.sent = self.sent, []
            return sent
        raise ValueError(f"unknown fake request {method!r}")


DEMO_SCRIPT = [fader(1, 0.6), press(1, "mute"), release(1, "mute"), knob(2, -3), bus_select("bus2")]


def main() -> None:
    """Run the real MIDI child against the fake and print what comes back."""
    environment = {**os.environ, BACKEND_ENVIRONMENT_VARIABLE: "fake", "PYTHONUNBUFFERED": "1"}
    requests = [
        {"id": 1, "method": "open", "params": {"match": "smc"}},
        {"id": 2, "method": "fake_inject", "params": {"messages": DEMO_SCRIPT}},
        {
            "id": 3,
            "method": "send",
            "params": {"messages": [[NOTE_ON, button_note(1, "mute"), 127]]},
        },
        {"id": 4, "method": "fake_sent"},
    ]
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    completed = subprocess.run(
        [sys.executable, "-m", "baja_studio.childprocs.midi_child"],
        input=stdin,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )
    print(completed.stdout, end="")
    print(completed.stderr, end="", file=sys.stderr)


if __name__ == "__main__":
    main()
