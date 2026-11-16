"""OBS crop driver (`obs_crop`): a digital PTZ that moves a Crop/Pad filter on an OBS source.

A 4K webcam cropped to a 1080p window gives a lossless 2x zoom with instant,
motor-free moves. The filter (not a scene item's transform) is moved so that
projector windows, which show sources with their filters, show the crop too.

The arbiter's calls only record what is wanted and wake the worker
(`camera:<id>:obs`). The worker, through its OBS's connection
(`obs_gateway.py`: playout's, or a remote OBS's from the cameras module's
pool, ADR 0021):

1. reads the filter (`obs_crop_sync.read_filter`) when it starts, after OBS
   comes back, and when OBS says the filter or source changed, and takes the
   crop OBS has as the window whenever it differs from what the studio last
   wrote, so connecting never moves the picture;
2. measures the source's size when it may have changed (`obs_crop_size.py`);
   motion waits while the size is unknown;
3. integrates the held velocity at `crop.update_hz` (`obs_crop_motion.py`),
   applies preset recalls and click-to-centre jumps, and sends
   `SetSourceFilterSettings` only when the pixel edges changed. Settings
   edits (a narrower `min_window`, another aspect) apply at the next move,
   never by themselves; only a new source size re-expresses the crop.

Echoes of its own writes are told from edits made in OBS by `obs_crop_edits.py`.

Health: OBS away is `offline` with its connection's reason (a remote OBS
that refused the password: `degraded`); a missing source is
`offline`; a missing, wrong-kind or disabled filter, or another filter that
changes the size, is `degraded`, with what to do. Filters are never created
or changed except by moving the crop (Camera setup's "Create filter" is an
explicit command).
"""

import collections
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_transport import (
    ObsError,
    ObsNotConnectedError,
    ObsRequestTimeoutError,
)
from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_window import CropWindow, WindowTarget
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.cameras.drivers.obs_crop_motion import CropCommands
from baja_studio.cameras.drivers.obs_crop_session import IDLE_SECONDS, CropSession, crop_frame
from baja_studio.cameras.drivers.obs_crop_state import CropPublisher, crop_state
from baja_studio.cameras.drivers.obs_crop_sync import CropUnavailableError
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST, ObsGateway
from baja_studio.core.supervisor import HealthState, WorkerContext, WorkerSpec

PRESET_MAX = 127
_HEALTH_STATE = {
    Status.OK: HealthState.OK,
    Status.DEGRADED: HealthState.DEGRADED,
    Status.OFFLINE: HealthState.OFFLINE,
}

SaveWindow = Callable[[int, CropWindow], None]


class ObsCropDriver(CameraDriver):
    """One crop camera: a Crop/Pad filter on an OBS source."""

    def __init__(
        self,
        host: DriverHost,
        gateway: ObsGateway,
        config: Callable[[], CameraConfig],
        save_window: SaveWindow,
    ) -> None:
        """`config` is read on every step, so edits apply at once; `save_window(n, window)`
        stores a preset in the camera's settings."""
        self._host = host
        self._gateway = gateway
        self._config = config
        self._save_window = save_window
        self.worker_name = f"{host.worker_prefix}:obs"
        self._commands = CropCommands(host.clock)
        self._session = CropSession(self._commands)
        self._inbox: collections.deque[tuple[str, dict[str, Any]]] = collections.deque()
        self._worker: WorkerContext | None = None
        self._publisher = CropPublisher(host.publish)
        self._synced_for: tuple[str, str] | None = None

    # --- CameraDriver -------------------------------------------------------------------

    def connect(self) -> None:
        """Start the worker; it reads OBS but changes nothing until told to move."""
        self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop the worker (the crop stays where it is in OBS)."""
        self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Hold `velocity` for `ttl_ms`; the worker moves the window."""
        self._commands.move(velocity, ttl_ms / 1000)
        self._wake()

    def stop(self) -> None:
        """Stop moving the window (nothing to send: it simply stays)."""
        self._commands.stop()
        self._wake()

    def recall_preset(self, n: int) -> None:
        """Jump to stored window `n` (from the camera's settings)."""
        self._commands.jump(n)
        self._wake()

    def store_preset(self, n: int) -> None:
        """Store the current window as preset `n` in the camera's settings, now.

        Saved before the command answers, so a page that reloads its settings
        afterwards sees it.
        """
        window = self._session.window
        if window is None:
            self._session.last_error = f"cannot store preset {n + 1}: the crop is not known yet"
            self._wake()
            return
        self._save_window(n, window)

    def place_window(self, target: WindowTarget) -> None:
        """Jump the window (click-to-centre, reset); any velocity stops."""
        self._commands.jump(target)
        self._wake()

    def position(self) -> Position | None:
        """The window's centre and zoom (the contract's crop position)."""
        return self._publisher.position

    def capabilities(self) -> DriverCapabilities:
        """A crop camera: position feedback (the window), zoom, presets 0-127, no auto."""
        return DriverCapabilities(CameraKind.CROP, True, True, False, PRESET_MAX)

    def health(self) -> Status:
        """ok, degraded (OBS is there but the filter is not) or offline."""
        return self._session.health[0]

    # --- the worker ---------------------------------------------------------------------

    def _wake(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.wake()

    def _run(self, worker: WorkerContext) -> None:
        self._worker = worker
        remove_listener = self._gateway.add_listener(self._on_obs_event)
        try:
            while not worker.should_stop:
                pause = self._step(self._host.clock.monotonic())
                status, reason = self._session.health
                if status != Status.OFFLINE:
                    worker.mark_online()
                worker.report_health(_HEALTH_STATE[status], reason)
                worker.sleep(pause)
        finally:
            remove_listener()
            self._worker = None

    def _step(self, now: float) -> float:
        """One look at OBS and one motion step; returns how long to sleep."""
        config = self._config()
        key = (config.obs_source or "", config.crop.filter_name)
        session = self._session
        if key != self._synced_for:
            self._synced_for = key
            session.source_changed()
        client, link = self._gateway.client(), self._gateway.link()
        pause = IDLE_SECONDS
        if client is None or not link.connected:
            # offline, or degraded when a remote OBS refused the password (ADR 0021).
            session.unavailable(link.status, link.problem, lost=True)
        else:
            while self._inbox:
                kind, data = self._inbox.popleft()
                session.event(kind, data, key[1], now)
            try:
                pause = session.step(client, key, config, now)
            except CropUnavailableError as unavailable:
                session.unavailable(unavailable.status, str(unavailable))
            except (ObsNotConnectedError, ObsRequestTimeoutError) as error:
                session.unavailable(Status.OFFLINE, link.lost(error), lost=True)
            except ObsError as error:
                session.unavailable(Status.DEGRADED, link.refused(error))
        self._publish(config, now)
        return pause

    def _on_obs_event(self, kind: str, data: dict[str, Any]) -> None:
        """Reader thread: queue the events about this camera's source, and wake the worker."""
        source = self._config().obs_source
        names = {data.get("sourceName"), data.get("inputName"), data.get("oldInputName")}
        if kind == CONNECTION_LOST or source in names:
            self._inbox.append((kind, data))
            self._wake()

    def _publish(self, config: CameraConfig, now: float) -> None:
        session = self._session
        size, window = session.sizes.size, session.window
        crop = crop_state(crop_frame(size, config), window, config) if size and window else None
        status, reason = session.health
        self._publisher.publish(
            now,
            moving=self._commands.moving,
            crop=crop,
            status=status,
            reason=reason,
            last_error=session.last_error,
        )
