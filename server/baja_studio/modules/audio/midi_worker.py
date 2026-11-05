"""The `midi` worker: runs the MIDI child process and carries messages both ways.

The child (`childprocs/midi_child.py`) owns the ports, so a missing or hung
device never stalls the service. This worker:

- starts the child and asks it to `open` the device matching
  `midi.device_name_match`; the child re-opens it whenever it reappears and
  tells us with `port` events. Health follows: `offline` with a message
  while the device is away, `ok` when open;
- hands `midi` events (on the child's reader thread) to the surface on this
  thread, fader moves coalesced to the latest per strip;
- sends the surface's LED and fader messages;
- pings the child every 2 s; a dead or hung child raises
  `DeviceOfflineError` and the supervisor starts a new one.

With MIDI turned off in the settings it runs no child and reports so.
"""

import collections
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from baja_studio.adapters.mvave_map import FaderMoved, MvaveEvent, decode
from baja_studio.audio.config import AudioSettings
from baja_studio.core.childproc import ChildRequestError, EventCallback
from baja_studio.core.worker import DeviceOfflineError, HealthState, WorkerContext
from baja_studio.modules.audio.publisher import AudioPublisher
from baja_studio.modules.audio.surface import MvaveSurface

PING_INTERVAL_SECONDS = 2.0
IDLE_INTERVAL_SECONDS = 1.0
REFRESH_INTERVAL_SECONDS = 10.0


class MidiChildHost(Protocol):
    """What the worker uses of `ChildProcessHost` (tests may pass an in-process one)."""

    @property
    def is_running(self) -> bool:
        """True while the child runs."""

    def add_exit_listener(self, listener: Callable[[], None]) -> None:
        """Call `listener` when the child exits."""

    def start(self) -> None:
        """Start the child."""

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a request and wait for the answer."""

    def ping(self) -> None:
        """Raise `DeviceOfflineError` unless the child answers."""

    def stop(self) -> None:
        """Stop the child."""


ChildFactory = Callable[[EventCallback], MidiChildHost]


class MidiWorker:
    """One MIDI child at a time, for the audio module."""

    def __init__(
        self,
        new_child: ChildFactory,
        surface: MvaveSurface,
        settings: Callable[[], AudioSettings],
        publisher: AudioPublisher,
        logger: logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """`new_child(on_event)` creates (not starts) a child host."""
        self._new_child = new_child
        self._surface = surface
        self._settings = settings
        self._publisher = publisher
        self._logger = logger
        self._inbox: collections.deque[tuple[str, Any]] = collections.deque()
        self._inbox_lock = threading.Lock()
        self._worker: WorkerContext | None = None
        self._host: MidiChildHost | None = None
        self._port_open = False
        # How many MIDI messages from the device have been handled (tests wait on it).
        self.messages_handled = 0

    def wake(self) -> None:
        """Something to send, or settings changed."""
        if self._worker is not None:
            self._worker.wake()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Ask the running child something (`list_ports`, `fake_inject`); for commands."""
        host = self._host
        if host is None or not host.is_running:
            raise DeviceOfflineError("the MIDI child is not running (MIDI turned off?)")
        return host.request(method, params)

    def run(self, worker: WorkerContext) -> None:
        """Worker body: idle while MIDI is off, else run a child until it fails or we stop."""
        self._worker = worker
        while not worker.should_stop:
            if not self._settings().midi.enabled:
                self._publisher.midi(
                    status="disabled", device=None, message="M-VAVE control is off"
                )
                worker.mark_online()
                worker.report_health(HealthState.OK, "M-VAVE control is off")
                worker.sleep(IDLE_INTERVAL_SECONDS)
                continue
            self._run_child(worker)

    def _run_child(self, worker: WorkerContext) -> None:
        """One child, until MIDI is turned off, the child fails, or we stop."""
        with self._inbox_lock:
            self._inbox.clear()
        host = self._new_child(self._on_child_event)
        host.add_exit_listener(worker.wake)
        self._publisher.midi(status="starting", message="")
        host.start()
        self._host = host
        try:
            self._serve(worker, host)
        finally:
            self._host = None
            self._port_open = False
            host.stop()

    def _serve(self, worker: WorkerContext, host: MidiChildHost) -> None:
        match = self._settings().midi.device_name_match
        self._take_port_status(worker, host.request("open", {"match": match}))
        clock = worker.clock
        next_ping = clock.monotonic() + PING_INTERVAL_SECONDS
        next_refresh = clock.monotonic() + REFRESH_INTERVAL_SECONDS
        while not worker.should_stop:
            midi = self._settings().midi
            if not midi.enabled:
                return  # the run loop idles until it is turned on again
            if midi.device_name_match != match:
                match = midi.device_name_match
                self._take_port_status(worker, host.request("open", {"match": match}))
            if not host.is_running:
                raise DeviceOfflineError("the MIDI child process exited")
            self._handle_inbox(worker)
            self._send_outgoing(host)
            now = clock.monotonic()
            if now >= next_refresh:
                next_refresh = now + REFRESH_INTERVAL_SECONDS
                self._surface.refresh(feedback_faders=False)
            if now >= next_ping:
                next_ping = now + PING_INTERVAL_SECONDS
                host.ping()
            worker.sleep(max(0.0, min(next_ping, next_refresh) - clock.monotonic()))

    def _handle_inbox(self, worker: WorkerContext) -> None:
        with self._inbox_lock:
            items = list(self._inbox)
            self._inbox.clear()
        events: list[MvaveEvent] = []
        for kind, data in items:
            if kind == "port":
                self._take_port_status(worker, data)
            elif kind == "midi":
                events.append(decode(data.get("bytes", [])))
        for event in _coalesce_faders(events):
            self._surface.handle(event)
        self.messages_handled += len(events)

    def _send_outgoing(self, host: MidiChildHost) -> None:
        messages = self._surface.take_outgoing()
        if not messages or not self._port_open:
            return
        try:
            host.request("send", {"messages": messages})
        except ChildRequestError as error:
            # The device went away mid-send; a `port` event follows and LEDs are re-sent on return.
            self._logger.info("MIDI send failed: %s", error)

    def _take_port_status(self, worker: WorkerContext, status: dict[str, Any]) -> None:
        state, device = status.get("state"), status.get("device")
        if state == "open" and self._port_open:
            return  # both the `open` reply and the child's `port` event say so
        if state == "open":
            self._port_open = True
            self._publisher.midi(status="ok", device=device, message="")
            worker.mark_online()
            worker.report_health(HealthState.OK)
            self._logger.info("M-VAVE open: %s", device)
            self._surface.port_opened()
            return
        self._port_open = False
        match = self._settings().midi.device_name_match
        message = (
            status.get("message") or f"no MIDI device named like {match!r}; plug in the M-VAVE"
        )
        self._publisher.midi(status="offline", device=None, message=message)
        worker.report_health(HealthState.OFFLINE, f"M-VAVE: {message}")

    def _on_child_event(self, event: str, data: Any) -> None:
        """Child reader thread: queue the event and wake the worker."""
        with self._inbox_lock:
            self._inbox.append((event, data))
        self.wake()


def _coalesce_faders(events: list[MvaveEvent]) -> list[MvaveEvent]:
    """Only the latest position of each fader in a batch (a sweep sends dozens)."""
    last_index = {
        event.strip: index for index, event in enumerate(events) if isinstance(event, FaderMoved)
    }
    return [
        event
        for index, event in enumerate(events)
        if not isinstance(event, FaderMoved) or last_index[event.strip] == index
    ]
