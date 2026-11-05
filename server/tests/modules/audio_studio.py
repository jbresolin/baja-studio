"""The audio module running inside the service, with fakes around it, for module tests.

Loaded into every test through `pytest_plugins` in conftest.py. The `audio`
fixture runs the service with only the audio module, on the fake clock.
Nothing real is opened:

- the XR18 is a `SimulatedXr18` reached through the in-process network
  (no UDP socket);
- the M-VAVE is a `FakeMidiBackend` inside a `MidiChild`, run in-process by
  `InProcessMidiHost` instead of as a child process (tests/childprocs
  checks the real child process separately).
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.adapters.osc import OscArgument
from baja_studio.childprocs.midi_child import MidiChild
from baja_studio.core.childproc import ChildRequestError, EventCallback
from baja_studio.core.module import ModuleContext
from baja_studio.core.services import StudioServices
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.fakes.fake_midi import FakeMidiBackend
from baja_studio.fakes.fake_xr18_in_process import InProcessXr18Network
from baja_studio.fakes.simulated_xr18 import SimulatedXr18
from baja_studio.modules.audio import AudioModule
from baja_studio.modules.audio.midi_worker import MidiChildHost
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time,
    step_fake_time_until,
    wait_until_workers_sleep,
)

MIXER_HOST = "192.168.1.10"
MIXER_PORT = 10024
AUDIO_WORKERS = module_worker_threads("audio")


class _EventsToHost:
    """Stands in for the child's stdout: events go straight to the host's callback."""

    def __init__(self, on_event: EventCallback) -> None:
        self._on_event = on_event

    def send_event(self, event: str, data: Any = None) -> None:
        self._on_event(event, data)

    def write(self, message: dict[str, Any]) -> None:
        pass  # replies are returned directly by `request`


class InProcessMidiHost:
    """A `MidiChild` with the fake backend, called directly: a child process without the process."""

    def __init__(self, backend: FakeMidiBackend, on_event: EventCallback) -> None:
        self.backend = backend
        self.child = MidiChild(backend, _EventsToHost(on_event))  # type: ignore[arg-type]
        self._running = False
        self._exit_listeners: list[Callable[[], None]] = []
        self.requests: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def add_exit_listener(self, listener: Callable[[], None]) -> None:
        self._exit_listeners.append(listener)

    def start(self) -> None:
        self._running = True

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self._running:
            raise DeviceOfflineError("child not running")
        self.requests.append(method)
        try:
            return self.child.handle(method, params or {})
        except Exception as error:
            raise ChildRequestError(f"{type(error).__name__}: {error}") from error

    def ping(self) -> None:
        if not self._running:
            raise DeviceOfflineError("child not running")

    def stop(self) -> None:
        self._running = False

    def crash(self) -> None:
        """Like the child process dying: listeners hear about it."""
        self._running = False
        for listener in self._exit_listeners:
            listener()


@dataclass
class AudioStudio:
    """The running service plus the fakes around it."""

    services: StudioServices
    module: AudioModule
    clock: FakeClock
    network: InProcessXr18Network
    mixer: SimulatedXr18
    midi_backend: FakeMidiBackend
    midi_hosts: list[InProcessMidiHost]
    http: TestClient

    def command(self, command: str, source: str = "web:alice", **args: Any) -> Any:
        return self.services.registry.dispatch_command("audio", command, args, source)

    def state(self, path: str = "") -> Any:
        return self.services.state.get("audio", path)

    def configure(self, **sections: Any) -> None:
        """Save settings sections the way the setup page does."""
        self.services.settings.update("audio", sections)

    def connect_mixer(self) -> None:
        """Point audio at the fake XR18 and wait until it answers and is read."""
        self.configure(mixer={"host": MIXER_HOST, "port": MIXER_PORT})
        self.soon(lambda: self.state("mixer.connection") == "ok")

        # The initial read is spread over the packet budget: let it all go,
        # so no late answer lands in the middle of a test.
        def initial_read_done() -> bool:
            link = self.module.controller._link
            return link is not None and link.pending_counts() == (0, 0)

        self.soon(initial_read_done)
        self.pass_time(0.1)

    def mixer_value(self, address: str) -> OscArgument:
        return self.mixer.values[address]

    def change_on_mixer(self, address: str, value: OscArgument) -> None:
        """Someone at Mixing Station changes `address`."""
        self.network.external_change(self.mixer, address, value)

    def press(self, *messages: list[int]) -> None:
        """The operator touches the M-VAVE; waits until the MIDI worker has handled it."""
        worker = self.module.midi_worker
        handled_before = worker.messages_handled
        self.midi_backend.inject([list(message) for message in messages])
        eventually(lambda: worker.messages_handled >= handled_before + len(messages))
        self.settle()

    def sent_midi(self) -> list[list[int]]:
        """MIDI the studio sent the device since the last call.

        Waits until the surface's queue is empty and the MIDI worker is back
        asleep, so nothing is still on its way.
        """
        surface = self.module.surface
        eventually(lambda: not surface.pending_outgoing())
        wait_until_workers_sleep(self.clock, AUDIO_WORKERS)
        sent, self.midi_backend.sent = self.midi_backend.sent, []
        return sent

    def soon(self, condition: Callable[[], bool], within_seconds: float = 6.0) -> None:
        """Let fake time pass in 0.1 s steps until `condition()` holds."""
        step_fake_time_until(self.clock, condition, within_seconds, AUDIO_WORKERS)

    def pass_time(self, seconds: float) -> None:
        """Let `seconds` of fake time pass, the workers running each step."""
        step_fake_time(self.clock, seconds, AUDIO_WORKERS)

    def settle(self) -> None:
        """Wait until every queued write has reached the mixer.

        A command wakes the mixer worker, which may not have run yet when the
        command returns; checking right away could see the mixer unchanged.
        """

        def nothing_left_to_send() -> bool:
            link = self.module.controller._link
            return link is None or link.pending_counts()[0] == 0

        self.soon(nothing_left_to_send)

    def log(self) -> list[tuple[str, str, str]]:
        """(source, command, result) of the audio log, newest first."""
        return [
            (entry["source"], entry["command"], entry["result"])
            for entry in self.services.command_log.tail("audio", 200)
        ]


@pytest.fixture
def audio(make_app: AppFactory, fake_clock: FakeClock) -> Iterator[AudioStudio]:
    network = InProcessXr18Network(fake_clock.monotonic)
    mixer = network.add_mixer(MIXER_HOST, MIXER_PORT, SimulatedXr18())
    backend = FakeMidiBackend()
    hosts: list[InProcessMidiHost] = []
    lock = threading.Lock()

    def midi_child(context: ModuleContext, on_event: EventCallback) -> MidiChildHost:
        host = InProcessMidiHost(backend, on_event)
        with lock:
            hosts.append(host)
        return host

    module = AudioModule(mixer_transport_factory=network.open, midi_child_factory=midi_child)
    app = make_app(lambda registry: [module])
    with TestClient(app) as http:
        studio = AudioStudio(
            app.state.services, module, fake_clock, network, mixer, backend, hosts, http
        )
        eventually(lambda: len(hosts) == 1, "the MIDI worker never started its child")
        yield studio
