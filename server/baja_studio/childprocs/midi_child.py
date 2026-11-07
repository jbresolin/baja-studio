"""Child process that owns the MIDI ports (M-VAVE SMC-Mixer), run by the audio module.

    python -m baja_studio.childprocs.midi_child

MIDI drivers can hang port enumeration or crash the interpreter (2026, on
Windows), so this runs apart from the service. Requests (see `childproc.py`):

- `open {match}`: use the first input and output whose name contains
  `match` (case-insensitive); opened now if present, else whenever it appears.
- `send {messages: [[status, data1, data2], ...]}`: send to the device.
- `status`: `{state: "open" | "offline" | "idle", device}`.
- `list_ports`: `{inputs: [...], outputs: [...]}`.
- `check`: look for the port now (a thread also does it every second).

Events: `midi {bytes}` for each message from the device, `port {state,
device, message}` when it appears or goes away. The backend is
python-rtmidi, or the fake (`fakes/fake_midi.py`) when the environment
variable `BAJA_STUDIO_MIDI_BACKEND` is `fake`; the fake also answers
`fake_*` requests (inject messages, unplug, read what was sent).
"""

import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from baja_studio.core.childproc_child import ChildChannel, run_child

BACKEND_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_MIDI_BACKEND"
CHECK_INTERVAL_SECONDS = 1.0

MessageCallback = Callable[[list[int]], None]


class MidiBackend(Protocol):
    """The few things the child needs from a MIDI library."""

    def input_names(self) -> list[str]:
        """Names of the MIDI inputs present now."""

    def output_names(self) -> list[str]:
        """Names of the MIDI outputs present now."""

    def open(self, input_name: str, output_name: str, on_message: MessageCallback) -> None:
        """Open both ports; `on_message` is called (on any thread) for each incoming message."""

    def send(self, message: list[int]) -> None:
        """Send one message; raises `OSError` if the device is gone."""

    def close(self) -> None:
        """Close whatever is open; never raises."""


class MidiChild:
    """The request handler and port watcher, independent of the real stdin/stdout."""

    def __init__(self, backend: MidiBackend, channel: ChildChannel) -> None:
        """Nothing is opened until the service asks with `open`."""
        self._backend = backend
        self._channel = channel
        self._lock = threading.Lock()
        self._match = ""
        self._device: str | None = None

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        """Called by `run_child` for each request."""
        if method == "open":
            with self._lock:
                self._match = str(params.get("match", "")).strip().lower()
                self._close_locked(report=False)
            self.check_port()
            return self.status()
        if method == "send":
            return self._send(params.get("messages", []))
        if method == "status":
            return self.status()
        if method == "list_ports":
            return {"inputs": self._backend.input_names(), "outputs": self._backend.output_names()}
        if method == "check":
            self.check_port()
            return self.status()
        if method.startswith("fake_"):
            fake_handler = getattr(self._backend, "handle_fake", None)
            if fake_handler is None:
                raise ValueError("fake_* requests need the fake MIDI backend")
            return fake_handler(method, params)
        raise ValueError(f"unknown method {method!r}")

    def status(self) -> dict[str, Any]:
        """Whether the device is open, and its name."""
        with self._lock:
            if not self._match:
                return {"state": "idle", "device": None}
            if self._device:
                return {"state": "open", "device": self._device}
            reason = getattr(self._backend, "unavailable_reason", None)
            return {"state": "offline", "device": None, **({"message": reason} if reason else {})}

    def check_port(self) -> None:
        """Notice the device leaving or arriving; called every second and on `check`."""
        with self._lock:
            if not self._match:
                return
            inputs = self._backend.input_names()
            if self._device is not None:
                if self._device not in inputs:
                    self._close_locked(report=True, message=f"{self._device} disconnected")
                return
            input_name = self._matching(inputs)
            output_name = self._matching(self._backend.output_names())
            if input_name is None or output_name is None:
                return
            try:
                self._backend.open(input_name, output_name, self._on_message)
            except OSError as error:
                self._channel.send_event(
                    "port", {"state": "offline", "device": None, "message": str(error)}
                )
                return
            self._device = input_name
            self._channel.send_event("port", {"state": "open", "device": input_name, "message": ""})

    def _send(self, messages: list[list[int]]) -> int:
        with self._lock:
            if self._device is None:
                raise OSError("the MIDI device is not connected")
            try:
                for message in messages:
                    self._backend.send([int(byte) & 0xFF for byte in message])
            except OSError as error:
                self._close_locked(report=True, message=f"sending failed: {error}")
                raise
        return len(messages)

    def _matching(self, names: list[str]) -> str | None:
        return next((name for name in names if self._match in name.lower()), None)

    def _close_locked(self, report: bool, message: str = "") -> None:
        if self._device is None:
            return
        self._backend.close()
        self._device = None
        if report:
            self._channel.send_event(
                "port", {"state": "offline", "device": None, "message": message}
            )

    def _on_message(self, message: list[int]) -> None:
        self._channel.send_event("midi", {"bytes": list(message)})


class UnavailableBackend:
    """Stands in when the MIDI system itself cannot be used (no driver, no ALSA sequencer).

    The child keeps running and answering, so the page can say why instead
    of the supervisor restarting a child that can never work.
    """

    def __init__(self, reason: str) -> None:
        """`reason` is shown on the page as the M-VAVE's status message."""
        self.unavailable_reason = reason

    def input_names(self) -> list[str]:
        """No ports."""
        return []

    def output_names(self) -> list[str]:
        """No ports."""
        return []

    def open(self, input_name: str, output_name: str, on_message: MessageCallback) -> None:
        """Never succeeds."""
        raise OSError(self.unavailable_reason)

    def send(self, message: list[int]) -> None:
        """Never succeeds."""
        raise OSError(self.unavailable_reason)

    def close(self) -> None:
        """Nothing to close."""


def make_backend() -> MidiBackend:
    """python-rtmidi, or the fake when `BAJA_STUDIO_MIDI_BACKEND=fake`."""
    if os.environ.get(BACKEND_ENVIRONMENT_VARIABLE) == "fake":
        from baja_studio.fakes.fake_midi import FakeMidiBackend

        return FakeMidiBackend()
    try:
        from baja_studio.childprocs.midi_rtmidi import RtMidiBackend

        return RtMidiBackend()
    except Exception as error:  # the library raises several kinds when there is no MIDI system
        return UnavailableBackend(f"MIDI is not available on this computer: {error}")


def watch_ports(child: MidiChild, interval_seconds: float = CHECK_INTERVAL_SECONDS) -> None:
    """Thread body: look for the device every second, for the life of the process."""
    while True:
        time.sleep(interval_seconds)
        try:
            child.check_port()
        except Exception as error:  # a driver error must not kill the watcher
            print(f"port check failed: {type(error).__name__}: {error}", file=sys.stderr)


def main() -> None:
    """Entry point when run as `python -m baja_studio.childprocs.midi_child`."""
    channel = ChildChannel()
    child = MidiChild(make_backend(), channel)
    threading.Thread(target=watch_ports, args=(child,), name="midi-port-watch", daemon=True).start()
    run_child(child.handle, channel=channel)


if __name__ == "__main__":
    main()
