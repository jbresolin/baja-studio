"""The real MIDI backend for the MIDI child: python-rtmidi.

Imported only inside the child process (never by the service or the tests),
so a broken MIDI driver can only take the child down.
"""

import contextlib
from typing import Any

import rtmidi

from baja_studio.childprocs.midi_child import MessageCallback


class RtMidiBackend:
    """Opens one input and one output port by name."""

    def __init__(self) -> None:
        """Two port-less objects used only to list ports."""
        self._probe_in: Any = rtmidi.MidiIn()
        self._probe_out: Any = rtmidi.MidiOut()
        self._in: Any = None
        self._out: Any = None

    def input_names(self) -> list[str]:
        """Inputs present now (the driver is asked again every call)."""
        return [str(name) for name in self._probe_in.get_ports()]

    def output_names(self) -> list[str]:
        """Outputs present now."""
        return [str(name) for name in self._probe_out.get_ports()]

    def open(self, input_name: str, output_name: str, on_message: MessageCallback) -> None:
        """Open both ports; rtmidi calls `on_message` on its own thread."""
        self.close()
        try:
            midi_in: Any = rtmidi.MidiIn()
            midi_in.open_port(self.input_names().index(input_name))
            # Sysex, clock and active sensing are noise for a control surface.
            midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
            midi_in.set_callback(lambda event, _data: on_message(list(event[0])))
            midi_out: Any = rtmidi.MidiOut()
            midi_out.open_port(self.output_names().index(output_name))
        except (ValueError, rtmidi.RtMidiError) as error:
            raise OSError(f"cannot open {input_name!r}: {error}") from error
        self._in, self._out = midi_in, midi_out

    def send(self, message: list[int]) -> None:
        """Send one message to the output port."""
        if self._out is None:
            raise OSError("output port not open")
        try:
            self._out.send_message(message)
        except rtmidi.RtMidiError as error:
            raise OSError(str(error)) from error

    def close(self) -> None:
        """Close both ports if open."""
        for port in (self._in, self._out):
            if port is not None:
                with contextlib.suppress(Exception):
                    port.close_port()
        self._in = self._out = None
