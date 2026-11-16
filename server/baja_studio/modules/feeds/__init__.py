"""The feeds module: remote pictures (drone, trackside laptops) through a bundled MediaMTX.

MediaMTX runs as its own Windows service (`BajaStudioFeeds`), not as a
child of the studio, so feeds and OBS keep going while the studio restarts
or upgrades. This module writes its config from the settings
(`mediamtx_config.py`, applied by `controller.py`), watches it through its
control API every two seconds, and adds a feed to OBS when the operator
asks (`obs_media_source.py`).

State (`state.feeds`):
- `service`: {name, status, answering, version, api, message, port_conflicts[], log_tail[]};
  `log_tail` (MediaMTX's last lines) only while it is not answering
- `config`: {path, written_at, waiting_for_reload, error}
- `feeds[]`: one card per feed (feed_status.py): live/waiting/unknown, since,
  protocol, codecs, size, bitrate, readers, the sender URLs and the URL OBS reads
- `other_paths[]`: live senders on names that are not feeds ("accept any name")
- `refused[]`: {name, at} of senders turned away for an unknown name
- `addresses[]`: the LAN addresses the sender URLs use
- `recordings`: {folder, free_bytes, any_feed_records}

Commands: `apply`, `obs_scenes`, `add_to_obs {feed, scene}`. Feeds are
edited through the settings (Feeds page), never by command, so a password
never reaches the command log. Readiness checks: `readiness.py`.
"""

import threading
from typing import Any

from pydantic import BaseModel

from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsGateway
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.supervisor import HealthState, WorkerContext, WorkerSpec
from baja_studio.core.system_services import (
    FEEDS_SERVICE,
    NotInstalledServiceControl,
    ServiceControl,
)
from baja_studio.core.worker import FailureStreak
from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxApiClient, MediamtxUnreachableError
from baja_studio.modules.feeds.controller import FeedsConfigController, FeedsConfigError
from baja_studio.modules.feeds.mediamtx_config import (
    config_path,
    recordings_root,
    write_default_config,
)
from baja_studio.modules.feeds.obs_media_source import add_media_source, obs_scene_names
from baja_studio.modules.feeds.publisher import FeedsStatePublisher
from baja_studio.modules.feeds.readiness import feeds_checks
from baja_studio.modules.feeds.system import FeedsSystem
from baja_studio.modules.feeds.urls import obs_read_url

__all__ = ["FeedsModule", "FeedsSystem"]

MEDIAMTX_WORKER = "mediamtx"
POLL_SECONDS = 2.0
# A refused config (a port taken) is tried again this often, and at every settings change.
CONFIG_RETRY_SECONDS = 30.0


class FeedsModule(Module):
    """Writes MediaMTX's config, watches its feeds, and adds them to OBS on request."""

    name = "feeds"
    title = "Feeds"
    SettingsModel = FeedsSettings

    def __init__(
        self,
        service_control: ServiceControl | None = None,
        system: FeedsSystem | None = None,
        obs: ObsGateway | None = None,
    ) -> None:
        """Defaults: no service, real network, no OBS; `main.py` passes the installed ones."""
        super().__init__()
        self._service_control = service_control or NotInstalledServiceControl()
        self._system = system or FeedsSystem()
        self._obs = obs or NoObsGateway()
        self._controller: FeedsConfigController | None = None
        self._publisher: FeedsStatePublisher | None = None
        self._config_error = ""
        self._apply_requested = threading.Event()
        self._subscribed = False
        self._retry_apply_at: float | None = None

    def initial_state(self) -> dict[str, Any]:
        """Nothing known about MediaMTX until the worker first asks."""
        return {
            "service": {
                "name": FEEDS_SERVICE,
                "status": "",
                "answering": False,
                "version": None,
                "api": "",
                "message": "",
                "port_conflicts": [],
                "log_tail": [],
            },
            "config": {"path": "", "written_at": None, "waiting_for_reload": False, "error": ""},
            "feeds": [],
            "other_paths": [],
            "refused": [],
            "addresses": [],
            "recordings": {"folder": "", "free_bytes": None, "any_feed_records": False},
        }

    def start(self, context: ModuleContext) -> None:
        """Make sure MediaMTX has a config file, and apply the settings once the worker runs."""
        super().start(context)
        self._controller = FeedsConfigController(
            context.data_directory,
            self._service_control,
            self._system.port_holders,
            context.clock,
            context.logger,
        )
        # As for the gateway: a file exists before this service first answers /api/health.
        write_default_config(context.data_directory, only_if_missing=True)
        context.update_state("config.path", str(config_path(context.data_directory)))
        self._publisher = FeedsStatePublisher(context, self._system, self._service_control)
        self._config_error = ""
        self._retry_apply_at = None
        self._apply_requested.set()
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._settings_changed)

    def workers(self) -> list[WorkerSpec]:
        """One worker: apply the settings when they change, and watch MediaMTX."""
        return [WorkerSpec(MEDIAMTX_WORKER, self._run_watcher, starts_offline=True)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`apply` (write the config now), `obs_scenes`, `add_to_obs {feed, scene}`."""
        if command == "apply":
            self._retry_apply_at = None
            self._apply_requested.set()
            self.context.supervisor.wake(self.name, MEDIAMTX_WORKER)
            return {"requested": True}
        if command == "obs_scenes":
            return {"scenes": obs_scene_names(self._obs)}
        if command == "add_to_obs":
            return self._add_to_obs(args)
        raise CommandError(
            f"feeds has no command {command!r}; try 'apply', 'obs_scenes' or 'add_to_obs'"
        )

    def readiness_checks(self) -> list[ReadinessCheck]:
        """MediaMTX, its ports, its config, the feeds, the recordings disk (`readiness.py`)."""
        settings = self._settings()
        return feeds_checks(
            settings,
            self._system,
            lambda: self.context.state.get(self.name) or {},
            recordings_root(settings, self.context.data_directory),
        )

    # --- the worker -----------------------------------------------------------------

    def _run_watcher(self, worker: WorkerContext) -> None:
        """Apply settings when asked, then read MediaMTX's state; every two seconds.

        The loop keeps running while MediaMTX is down (it reports `offline`
        instead of raising), so a settings change that fixes the cause (a
        port) is written at once rather than after the supervisor's backoff.
        """
        assert self._controller is not None and self._publisher is not None
        failures = FailureStreak()
        while not worker.should_stop:
            settings = self._settings()
            hold_back = self._apply_if_requested(settings)
            client = MediamtxApiClient(f"127.0.0.1:{settings.api_port}", self._system.api_transport)
            try:
                info = client.info()
                snapshots = client.paths()
                running = client.global_config() if self._controller.waiting_for_reload else None
            except MediamtxUnreachableError as error:
                reason = self._publisher.publish_down(settings, str(error))
                if failures.record(error):
                    self.context.logger.warning("feeds offline: %s", reason)
                worker.report_health(HealthState.OFFLINE, reason)
                worker.sleep(hold_back or POLL_SECONDS)
                continue
            if failures.recover():
                self.context.logger.info("MediaMTX answers again")
            reload_problem = self._controller.verify(running) if running is not None else ""
            self._publisher.publish_up(
                settings, info, snapshots, self._controller.waiting_for_reload
            )
            problems = [problem for problem in (self._config_error, reload_problem) if problem]
            worker.mark_online()
            worker.report_health(
                HealthState.DEGRADED if problems else HealthState.OK, "; ".join(problems)
            )
            worker.sleep(hold_back or POLL_SECONDS)

    def _apply_if_requested(self, settings: FeedsSettings) -> float:
        """Write the config if asked and allowed; returns seconds to wait if it was held back."""
        assert self._controller is not None
        if not self._apply_requested.is_set():
            return 0.0
        now = self.context.clock.monotonic()
        if self._retry_apply_at is not None and now < self._retry_apply_at:
            return 0.0
        wait = self._controller.seconds_until_write_allowed()
        if wait > 0:
            return min(wait, POLL_SECONDS)
        self._apply_requested.clear()
        try:
            written = self._controller.apply_if_needed(settings, self._answers(settings))
        except FeedsConfigError as error:
            if str(error) != self._config_error:
                self.context.logger.warning("feeds settings not applied: %s", error)
            self._config_error = str(error)
            self.context.update_state("config.error", self._config_error)
            # Try again later by itself: the program on the port may be closed meanwhile.
            self._apply_requested.set()
            self._retry_apply_at = now + CONFIG_RETRY_SECONDS
            return 0.0
        self._retry_apply_at = None
        self._config_error = ""
        self.context.update_state("config.error", "")
        if written:
            self.context.logger.info("MediaMTX config written")
            self.context.update_state("config.written_at", self.context.clock.now_utc().isoformat())
        return 0.0

    def _answers(self, settings: FeedsSettings) -> bool:
        """Whether a MediaMTX answers on the API port `settings` name."""
        client = MediamtxApiClient(f"127.0.0.1:{settings.api_port}", self._system.api_transport)
        try:
            client.info()
        except MediamtxUnreachableError:
            return False
        return True

    # --- commands and settings ------------------------------------------------------

    def _add_to_obs(self, args: dict[str, Any]) -> dict[str, Any]:
        settings = self._settings()
        feed = settings.feed_named(str(args.get("feed", "")))
        if feed is None:
            raise CommandError(f"there is no feed named {args.get('feed')!r}")
        scene = str(args.get("scene", "")).strip()
        if not scene:
            raise CommandError("pick the OBS scene the source goes into")
        result = add_media_source(
            self._obs, scene, feed.obs_source_name, obs_read_url(feed.name, settings)
        )
        self.context.logger.info("added feed %s to OBS scene %s", feed.name, scene)
        return result

    def _settings(self) -> FeedsSettings:
        settings = self.context.current_settings()
        assert isinstance(settings, FeedsSettings)
        return settings

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: have the worker write the new config right away."""
        if self._context is None:
            return
        self._retry_apply_at = None
        self._apply_requested.set()
        self.context.supervisor.wake(self.name, MEDIAMTX_WORKER)
