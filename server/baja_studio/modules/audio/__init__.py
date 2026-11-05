"""The audio module: fast operator controls for the XR18 (LIVESTACK-9, WP4).

The XR18 over OSC (`mixer` worker), the M-VAVE SMC-Mixer through a MIDI
child process (`midi` worker), the Ulanzi D200's HTTP actions at the
service root, and the audio pages. All of them change the mixer through
one `AudioController`, so they are peers.

Health: `offline` while the XR18 is not answering (nothing works without
it); `degraded` while only the M-VAVE is missing; `ok` otherwise.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from baja_studio.adapters.xr18_transport import TransportFactory, open_udp_transport
from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.stores import LinkStore, StoredLevels, create_audio_tables
from baja_studio.core.childproc import EventCallback
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.supervisor import Health, HealthState, WorkerSpec
from baja_studio.modules.audio.commands import DRAG_COMMANDS, run_command
from baja_studio.modules.audio.controller import AudioController
from baja_studio.modules.audio.d200_routes import d200_routes
from baja_studio.modules.audio.midi_worker import MidiChildHost, MidiWorker
from baja_studio.modules.audio.mixer_worker import MixerWorker
from baja_studio.modules.audio.publisher import AudioPublisher, initial_audio_state
from baja_studio.modules.audio.surface import MvaveSurface

MIDI_CHILD_MODULE = "baja_studio.childprocs.midi_child"
D200_SEEN_PUBLISH_SECONDS = 5.0

MidiChildFactory = Callable[[ModuleContext, EventCallback], MidiChildHost]


def real_midi_child(context: ModuleContext, on_event: EventCallback) -> MidiChildHost:
    """The MIDI child process (python-rtmidi, or the fake with BAJA_STUDIO_MIDI_BACKEND=fake)."""
    return context.new_child_process(MIDI_CHILD_MODULE, on_event)


def summarise_health(workers: dict[str, Health]) -> Health:
    """The module's one health from its two workers (see the module docstring)."""
    mixer = workers.get("mixer", Health(HealthState.OK))
    midi = workers.get("midi", Health(HealthState.OK))
    if mixer.state != HealthState.OK:
        return mixer
    if midi.state != HealthState.OK:
        return Health(HealthState.DEGRADED, midi.message)
    return Health(HealthState.OK, mixer.message or midi.message)


class AudioModule(Module):
    """Wires the audio parts together and runs their workers."""

    name = "audio"
    title = "Audio"
    SettingsModel = AudioSettings

    def __init__(
        self,
        mixer_transport_factory: TransportFactory = open_udp_transport,
        midi_child_factory: MidiChildFactory = real_midi_child,
    ) -> None:
        """The defaults are the real network and MIDI child; tests pass fakes."""
        super().__init__()
        self._mixer_transport_factory = mixer_transport_factory
        self._midi_child_factory = midi_child_factory
        self._controller: AudioController | None = None
        self._surface: MvaveSurface | None = None
        self._mixer_worker: MixerWorker | None = None
        self._midi_worker: MidiWorker | None = None
        self._subscribed = False
        self._d200_published_at: float | None = None

    @property
    def controller(self) -> AudioController:
        """The running controller (tests look inside)."""
        if self._controller is None:
            raise CommandError("audio is not running")
        return self._controller

    @property
    def midi_worker(self) -> MidiWorker:
        """The MIDI worker (tests wait on what it has handled)."""
        assert self._midi_worker is not None
        return self._midi_worker

    @property
    def surface(self) -> MvaveSurface:
        """The running M-VAVE surface."""
        if self._surface is None:
            raise CommandError("audio is not running")
        return self._surface

    def initial_state(self) -> dict[str, Any]:
        """No mixer, no MIDI, every channel unknown."""
        return initial_audio_state()

    def settings(self) -> AudioSettings:
        """Current settings, read fresh on every use so edits apply at once."""
        values = self.context.current_settings()
        assert isinstance(values, AudioSettings)
        return values

    def start(self, context: ModuleContext) -> None:
        """Create the tables and the parts; workers start next."""
        super().start(context)
        create_audio_tables(context.database)
        publisher = AudioPublisher(context.update_state)
        controller = AudioController(
            self.settings,
            StoredLevels(context.database, context.clock),
            LinkStore(context.database, defaults.LINKS),
            publisher,
            context.logger,
            wake_mixer=lambda: self._mixer_worker.wake() if self._mixer_worker else None,
        )
        surface = MvaveSurface(
            controller,
            context.clock,
            context.logger,
            log_action=lambda command, args, result: context.command_log.append(
                self.name, "device:mvave", command, args, result
            ),
            wake_midi=lambda: self._midi_worker.wake() if self._midi_worker else None,
        )
        self._mixer_worker = MixerWorker(
            controller, self.settings, self._mixer_transport_factory, context.logger
        )
        self._midi_worker = MidiWorker(
            lambda on_event: self._midi_child_factory(context, on_event),
            surface,
            self.settings,
            publisher,
            context.logger,
        )
        self._controller, self._surface = controller, surface
        publisher.publish("links", controller.links.matrix())
        publisher.publish("stored_levels", controller.stored.all())
        publisher.publish("settings_version", context.settings.record(self.name).version)
        controller.settings_changed()
        context.supervisor.set_health_summary(self.name, summarise_health)
        if not self._subscribed:
            context.settings.subscribe(self.name, self._settings_changed)
            self._subscribed = True

    def stop(self) -> None:
        """Workers have stopped (socket and child closed); drop the parts."""
        self._controller = None
        self._surface = None

    def workers(self) -> list[WorkerSpec]:
        """The XR18 connection and the MIDI child."""
        assert self._mixer_worker and self._midi_worker
        return [
            WorkerSpec("mixer", self._mixer_worker.run, starts_offline=True),
            WorkerSpec("midi", self._midi_worker.run, starts_offline=True),
        ]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """See `modules/audio/commands.py` for the list."""
        assert self._midi_worker is not None
        return run_command(
            self.controller,
            self.surface,
            self._midi_worker,
            self.context.settings,
            self.name,
            command,
            args,
            source,
        )

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Everything but a dragged fader's in-between values."""
        return failed or not (command in DRAG_COMMANDS and args.get("dragging") is True)

    def root_routes(self) -> APIRouter:
        """The D200 contract (`/action/...`)."""
        return d200_routes()

    def note_d200_request(self) -> None:
        """A D200 called; published at most every few seconds (it polls every second)."""
        if self._controller is None:
            return
        now = self.context.clock.monotonic()
        if (
            self._d200_published_at is not None
            and now - self._d200_published_at < D200_SEEN_PUBLISH_SECONDS
        ):
            return
        self._d200_published_at = now
        self._controller.publisher.publish(
            "d200", {"last_seen": self.context.clock.now_utc().isoformat()}
        )

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: apply at once."""
        controller = self._controller
        if controller is None or not isinstance(values, AudioSettings):
            return
        controller.publisher.publish(
            "settings_version", self.context.settings.record(self.name).version
        )
        controller.settings_changed()
        if self._mixer_worker is not None:
            self._mixer_worker.wake()
        if self._midi_worker is not None:
            self._midi_worker.wake()
        if self._surface is not None:
            self._surface.refresh(feedback_faders=True)
