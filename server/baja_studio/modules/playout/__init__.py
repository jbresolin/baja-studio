"""The playout module: queue and play finished clips in OBS (LIVESTACK-8, WP5).

Library from the local folders plus the orchestrator's manifest, a queue,
flash-free program switching through two OBS media sources, post-clip
behaviour, session persistence, the aired log and the on-air webhook.

WP5b added playlists, the "Return to" control, on-air actions (commands to
other modules when a clip starts or ends, sent through `dispatch`, the
registry's command path) and the hosts view.

Workers: `obs` (connection, events, 5 Hz position), `scanner` (folders
every 5 s), `probe` (ffprobe/thumbnails), `sync` (orchestrator manifest),
`hooks` (webhook POSTs) and `actions` (on-air actions). The module is as
healthy as its most worrying worker: OBS away makes it `offline`, a failed
cue, an unreadable folder or an unreachable orchestrator `degraded`; library
and queue work regardless.

Readiness checks: `readiness.py`.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
from fastapi import APIRouter
from pydantic import BaseModel

from baja_studio.adapters.obs_transport import TransportFactory, open_websocket_transport
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.core.hosts_cards import HostsCard
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.supervisor import WorkerSpec
from baja_studio.core.system_probes import free_disk_bytes as real_free_disk_bytes
from baja_studio.modules.playout.airing import AiringBookkeeper
from baja_studio.modules.playout.commands import run_command
from baja_studio.modules.playout.controller import PlayoutController
from baja_studio.modules.playout.hosts_cards import playout_hosts_cards
from baja_studio.modules.playout.library_workers import LibraryWorkers, ProberFactory
from baja_studio.modules.playout.obs_worker import ObsConnectionWorker
from baja_studio.modules.playout.publisher import PlayoutPublisher, initial_playout_state
from baja_studio.modules.playout.readiness import playout_checks
from baja_studio.modules.playout.routes import playout_routes
from baja_studio.playout.config import PlayoutSettings, ToolsSettings
from baja_studio.playout.hooks import WebhookNotifier
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.on_air import Dispatch, OnAirActionRunner
from baja_studio.playout.playlists import PlaylistStore
from baja_studio.playout.probe import MediaProber, Prober, tools_status
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.schema import create_playout_tables
from baja_studio.playout.switcher import ProgramSwitcher
from baja_studio.playout.tools import resolve_tools


def real_prober(tools: ToolsSettings) -> Prober:
    """ffprobe/ffmpeg: the installer's copies, or the configured paths (`playout/tools.py`)."""
    ffprobe, ffmpeg = resolve_tools(tools)
    return MediaProber(ffprobe.command, ffmpeg.command)


class PlayoutModule(Module):
    """Wires playout's parts together and runs its workers."""

    name = "playout"
    title = "Playout"
    SettingsModel = PlayoutSettings

    def __init__(
        self,
        obs_transport_factory: TransportFactory = open_websocket_transport,
        orchestrator_transport: httpx2.BaseTransport | None = None,
        prober_factory: ProberFactory = real_prober,
        hook_transport: httpx2.BaseTransport | None = None,
        free_disk_bytes: Callable[[Path], int] = real_free_disk_bytes,
        dispatch: Dispatch | None = None,
        find_tool: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """The defaults are the real network, tools and disks; tests pass fakes.

        `dispatch` is the registry's `dispatch_command`, for on-air actions.
        """
        super().__init__()
        self._free_disk_bytes = free_disk_bytes
        self._dispatch = dispatch
        self._find_tool = find_tool
        self._actions: OnAirActionRunner | None = None
        self._obs_client: ObsWebsocketClient | None = None
        self._obs_transport_factory = obs_transport_factory
        self._orchestrator_transport = orchestrator_transport
        self._prober_factory = prober_factory
        self._hook_transport = hook_transport
        self._controller: PlayoutController | None = None
        self._obs_worker: ObsConnectionWorker | None = None
        self._library_workers: LibraryWorkers | None = None
        self._notifier: WebhookNotifier | None = None
        self._subscribed = False
        self._last_tools: ToolsSettings | None = None

    @property
    def controller(self) -> PlayoutController:
        """The running controller (tests use it to look inside)."""
        if self._controller is None:
            raise CommandError("playout is not running")
        return self._controller

    def initial_state(self) -> dict[str, Any]:
        """Empty library, queue and program; OBS not configured."""
        return initial_playout_state()

    def settings(self) -> PlayoutSettings:
        """Current settings, read fresh on every use so edits apply at once."""
        values = self.context.current_settings()
        assert isinstance(values, PlayoutSettings)
        return values

    def start(self, context: ModuleContext) -> None:
        """Create the tables and parts, restore the session; workers start next."""
        super().start(context)
        create_playout_tables(context.database)
        library = LibraryStore(context.database, context.clock)
        queue = QueueStore(context.database, context.clock)
        playlists = PlaylistStore(context.database, context.clock)
        publisher = PlayoutPublisher(context.update_state, library, queue, playlists)
        client = ObsWebsocketClient(context.clock, self._obs_transport_factory)
        self._obs_client = client
        switcher = ProgramSwitcher(client, lambda: self.settings().obs, context.clock)
        self._notifier = WebhookNotifier(
            context.clock,
            context.logger,
            self._hook_transport,
            wake_worker=lambda: context.supervisor.wake(self.name, "hooks"),
        )
        self._actions = OnAirActionRunner(
            self._dispatch,
            context.logger,
            on_failure=publisher.publish_notice,
            wake_worker=lambda: context.supervisor.wake(self.name, "actions"),
        )
        airing = AiringBookkeeper(
            context.clock,
            self.settings,
            library,
            queue,
            playlists,
            self._notifier,
            self._actions,
            publisher,
        )
        controller = PlayoutController(
            context.clock,
            self.settings,
            library,
            queue,
            playlists,
            switcher,
            airing,
            publisher,
            context.logger,
        )
        controller.restore_session()
        self._publish_export_marker(publisher)
        self._publish_tools(publisher)
        self._obs_worker = ObsConnectionWorker(client, controller, self.settings, context.logger)
        self._library_workers = LibraryWorkers(
            context.clock,
            self.settings,
            library,
            publisher,
            context.data_directory,
            self._prober_factory,
            self._orchestrator_transport,
            context.logger,
            wake_probe=lambda: context.supervisor.wake(self.name, "probe"),
        )
        self._last_tools = self.settings().tools
        self._controller = controller
        if not self._subscribed:
            context.settings.subscribe(self.name, self._settings_changed)
            self._subscribed = True

    def stop(self) -> None:
        """Workers have stopped (the OBS worker closed its connection); drop the parts."""
        self._controller = None

    def workers(self) -> list[WorkerSpec]:
        """OBS connection, folder scanner, prober, orchestrator sync, webhook and action senders."""
        assert self._obs_worker and self._library_workers and self._notifier and self._actions
        return [
            WorkerSpec("obs", self._obs_worker.run, starts_offline=True),
            WorkerSpec("scanner", self._library_workers.run_scanner),
            WorkerSpec("probe", self._library_workers.run_probe),
            WorkerSpec("sync", self._library_workers.run_sync),
            WorkerSpec("hooks", self._notifier.run),
            WorkerSpec("actions", self._actions.run),
        ]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """See `modules/playout/commands.py` for the list."""
        return run_command(self.controller, self.context.settings, self.name, command, args, source)

    def readiness_checks(self) -> list[ReadinessCheck]:
        """OBS, its scenes, folders, tools, orchestrator and queue (see `readiness.py`)."""
        assert self._obs_client is not None
        return playout_checks(
            self.settings(),
            self._obs_client,
            self.controller,
            self.context.state.get(self.name) or {},
            self._free_disk_bytes,
        )

    def hosts_cards(self) -> list[HostsCard]:
        """ "Now playing" and "Next up" for the hosts screen (see `hosts_cards.py`)."""
        return playout_hosts_cards()

    def routes(self) -> APIRouter:
        """Clip media and thumbnails."""
        return playout_routes(lambda: self._controller.library if self._controller else None)

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: apply at once (wake the workers that read them)."""
        controller = self._controller
        if controller is None or not isinstance(values, PlayoutSettings):
            return
        self._publish_export_marker(controller.publisher)
        if values.tools != self._last_tools:
            # A fixed ffprobe path deserves another try at the files that failed.
            controller.library.clear_probe_errors()
            self._last_tools = values.tools
            self._publish_tools(controller.publisher)
        if self._obs_worker is not None:
            self._obs_worker.wake()
        for worker_name in ("scanner", "probe", "sync"):
            self.context.supervisor.wake(self.name, worker_name)

    def _publish_tools(self, publisher: PlayoutPublisher) -> None:
        tools = self.settings().tools
        publisher.publish_tools(
            tools_status(tools.ffprobe_path, tools.ffmpeg_path, self._find_tool)
        )

    def _publish_export_marker(self, publisher: PlayoutPublisher) -> None:
        marker = self.settings().export.stream_start_marker
        publisher.publish_export(marker.isoformat() if marker is not None else None)
