"""OBS crop driver (`obs_crop`): a digital PTZ that moves a Crop/Pad filter on an OBS source.

A 4K webcam cropped to a 1080p window gives a lossless 2x zoom with instant,
motor-free moves. The filter (not a scene item's transform) is moved so that
projector windows, which show sources with their filters, show the crop too.

The arbiter's calls only record what is wanted and wake the worker
(`camera:<id>:obs`). The worker, through playout's OBS connection
(`obs_gateway.py`):

1. reads the filter (`obs_crop_sync.read_filter`) when it starts, after OBS
   comes back, and when OBS says the filter or source changed;
2. measures the source's size (`measure_source`) when it does not know it or
   the source's settings changed (a new resolution);
3. adopts the crop OBS already has as the window, so connecting never moves
   the picture;
4. integrates the held velocity at `crop.update_hz` (`obs_crop_motion.py`),
   applies preset recalls and click-to-centre jumps, and sends
   `SetSourceFilterSettings` only when the pixel edges changed.

Its own changes come back as `SourceFilterSettingsChanged` events; one that
matches what was written, or arrives within `WRITE_PROTECTION_SECONDS` of a
write, is an echo. Any other is an edit made in OBS, and is adopted.

Health: OBS away is `offline` with playout's reason; a missing source is
`offline`; a missing, wrong-kind or disabled filter is `degraded`, with what
to do. Filters are never created or changed except by moving the crop
(Camera setup's "Create filter" is an explicit command).
"""

import collections
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import (
    ObsError,
    ObsNotConnectedError,
    ObsRequestTimeoutError,
)
from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_window import (
    CropFrame,
    CropSpeeds,
    CropWindow,
    PixelCrop,
    WindowTarget,
)
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.cameras.drivers.obs_crop_motion import CropMotion
from baja_studio.cameras.drivers.obs_crop_state import CropPublisher, crop_state
from baja_studio.cameras.drivers.obs_crop_sync import (
    QUIET_BEFORE_MEASURING_SECONDS,
    CropUnavailableError,
    FilterReading,
    measure_source,
    read_filter,
)
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST, ObsGateway
from baja_studio.core.supervisor import HealthState, WorkerContext, WorkerSpec

PRESET_MAX = 127
# How long OBS's echo of our own write is not taken for an edit made in OBS.
WRITE_PROTECTION_SECONDS = 0.5
IDLE_SECONDS = 1.0
MEASURE_RETRY_SECONDS = 0.1
# Events that mean "read the filter again".
FILTER_EVENTS = frozenset(
    {
        "SourceFilterCreated",
        "SourceFilterRemoved",
        "SourceFilterNameChanged",
        "SourceFilterEnableStateChanged",
        "InputNameChanged",
        "InputRemoved",
        "InputCreated",
    }
)
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
        stores a preset in the camera's settings (called from the worker)."""
        self._host = host
        self._gateway = gateway
        self._config = config
        self._save_window = save_window
        self.worker_name = f"{host.worker_prefix}:obs"
        self._lock = threading.Lock()
        self._motion = CropMotion()
        self._jump: WindowTarget | int | None = None  # a target, or a preset number
        self._stores: list[int] = []
        self._inbox: collections.deque[tuple[str, dict[str, Any]]] = collections.deque()
        self._worker: WorkerContext | None = None
        self._publisher = CropPublisher(host.publish)
        # What is known about OBS (worker thread only).
        self._synced_for: tuple[str, str] | None = None
        self._needs_sync = True
        self._needs_measure = True
        self._adopt = True
        self._reading: FilterReading | None = None
        self._source_size: tuple[int, int] | None = None
        self._size_hint: tuple[int, int] | None = None
        self._window: CropWindow | None = None
        self._written: PixelCrop | None = None
        self._last_write_at = -1e9
        self._health = (Status.OFFLINE, "starting")
        self._last_error = ""

    # --- CameraDriver -------------------------------------------------------------------

    def connect(self) -> None:
        """Start the worker; it reads OBS but changes nothing until told to move."""
        self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop the worker (the crop stays where it is in OBS)."""
        self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Hold `velocity` for `ttl_ms`; the worker moves the window."""
        with self._lock:
            self._motion.move(velocity, ttl_ms / 1000, self._host.clock.monotonic())
        self._wake()

    def stop(self) -> None:
        """Stop moving the window (nothing to send: it simply stays)."""
        with self._lock:
            self._motion.stop()
        self._wake()

    def recall_preset(self, n: int) -> None:
        """Jump to stored window `n` (from the camera's settings)."""
        with self._lock:
            self._motion.stop()
            self._jump = n
        self._wake()

    def store_preset(self, n: int) -> None:
        """Store the current window as preset `n` (saved by the worker)."""
        with self._lock:
            self._stores.append(n)
        self._wake()

    def place_window(self, target: WindowTarget) -> None:
        """Jump the window (click-to-centre, reset); any velocity stops."""
        with self._lock:
            self._motion.stop()
            self._jump = target
        self._wake()

    def position(self) -> Position | None:
        """The window's centre and zoom (the contract's crop position)."""
        return self._publisher.position

    def capabilities(self) -> DriverCapabilities:
        """A crop camera: position feedback (the window), zoom, presets 0-127, no auto."""
        return DriverCapabilities(CameraKind.CROP, True, True, False, PRESET_MAX)

    def health(self) -> Status:
        """ok, degraded (OBS is there but the filter is not) or offline."""
        return self._health[0]

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
                status, reason = self._health
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
        if key != self._synced_for:
            self._needs_sync = self._needs_measure = self._adopt = True
            self._synced_for = key
        client = self._gateway.client()
        link = self._gateway.link()
        if client is None or not link.connected:
            self._lost(f"OBS offline: {link.reason}")
            return IDLE_SECONDS
        self._handle_inbox(now)
        try:
            pause = self._talk_to_obs(client, key, config, now)
        except CropUnavailableError as unavailable:
            self._unavailable(unavailable.status, str(unavailable))
            return IDLE_SECONDS
        except (ObsNotConnectedError, ObsRequestTimeoutError) as error:
            self._lost(f"OBS offline: {error}")
            return IDLE_SECONDS
        except ObsError as error:
            self._unavailable(Status.DEGRADED, f"OBS: {error}")
            return IDLE_SECONDS
        self._store_presets()
        self._publish(config, now)
        return pause

    def _talk_to_obs(
        self, client: ObsRequestsMixin, key: tuple[str, str], config: CameraConfig, now: float
    ) -> float:
        source, filter_name = key
        if self._needs_sync:
            self._reading = read_filter(client, source, filter_name)
            self._written = self._reading.crop
            self._needs_sync = False
        if self._needs_measure:
            if now - self._last_write_at < QUIET_BEFORE_MEASURING_SECONDS:
                return MEASURE_RETRY_SECONDS
            if not self._measure(client, key):
                return MEASURE_RETRY_SECONDS
        assert self._source_size is not None and self._written is not None
        frame = _frame(self._source_size, config)
        if self._adopt or self._window is None:
            self._window, self._adopt = frame.window_of(self._written), False
        window = self._commanded_window(frame, config, now)
        if window != self._window:
            self._window = window
            pixels = frame.pixels(window)
            if pixels != self._written:
                client.set_source_filter_settings(source, filter_name, pixels.filter_settings())
                self._written, self._last_write_at = pixels, now
        assert self._reading is not None
        if not self._reading.enabled:
            self._health = (
                Status.DEGRADED,
                f"filter '{filter_name}' is switched off in OBS (its eye): the picture is not "
                "cropped",
            )
        else:
            self._health = (Status.OK, "")
        with self._lock:
            moving = self._motion.moving
        return 1.0 / config.crop.update_hz if moving else IDLE_SECONDS

    def _measure(self, client: ObsRequestsMixin, key: tuple[str, str]) -> bool:
        """Learn the source's size; False to try again shortly."""
        source, filter_name = key
        reading = read_filter(client, source, filter_name)
        try:
            size = measure_source(client, source, filter_name, reading)
        except CropUnavailableError:
            if self._size_hint is None or self._window is None:
                raise
            # The source shrank under a crop that no longer fits (OBS shows nothing):
            # re-express the window for the size its settings name, then measure again.
            size, self._size_hint = self._size_hint, None
            frame = _frame(size, self._config())
            pixels = frame.pixels(frame.clamp(self._window))
            client.set_source_filter_settings(source, filter_name, pixels.filter_settings())
            self._written, self._last_write_at = pixels, self._host.clock.monotonic()
            self._source_size = size
            return False
        if size is None:
            return False
        self._reading, self._written = reading, reading.crop
        self._source_size, self._needs_measure, self._size_hint = size, False, None
        return True

    def _commanded_window(self, frame: CropFrame, config: CameraConfig, now: float) -> CropWindow:
        """The window after this step's jump and motion (clamped to the current settings)."""
        assert self._window is not None
        with self._lock:
            jump, self._jump = self._jump, None
            step = self._motion.advance(now)
        window = self._window
        if isinstance(jump, WindowTarget):
            window = (
                frame.whole()
                if jump.whole
                else frame.centred_on(window, jump.centre_x, jump.centre_y)
            )
        elif jump is not None:
            stored = config.crop.window(jump)
            if stored is None:
                self._last_error = f"preset {jump + 1} is not stored"
            else:
                window = CropWindow(stored.centre_x, stored.centre_y, stored.width)
        if step.seconds > 0:
            speeds = CropSpeeds(
                config.crop.pan_speed, config.crop.tilt_speed, config.crop.zoom_speed
            )
            window = frame.drive(window, step.velocity, step.seconds, speeds)
        return frame.clamp(window)

    def _store_presets(self) -> None:
        with self._lock:
            stores, self._stores = self._stores, []
        for n in stores:
            if self._window is None:
                self._last_error = f"cannot store preset {n + 1}: the crop is not known yet"
            else:
                self._save_window(n, self._window)

    def _lost(self, reason: str) -> None:
        """OBS is away: stop moving, and read everything again once it is back."""
        self._needs_sync = self._needs_measure = self._adopt = True
        self._unavailable(Status.OFFLINE, reason)

    def _unavailable(self, status: Status, reason: str) -> None:
        self._needs_sync = True
        with self._lock:
            self._motion.stop()
            self._jump = None
        self._health = (status, reason)
        self._publish(self._config(), self._host.clock.monotonic())

    # --- OBS events (reader thread: queue and wake) ---------------------------------------

    def _on_obs_event(self, kind: str, data: dict[str, Any]) -> None:
        source = self._config().obs_source
        names = {data.get("sourceName"), data.get("inputName"), data.get("oldInputName")}
        if kind == CONNECTION_LOST or source in names:
            self._inbox.append((kind, data))
            self._wake()

    def _handle_inbox(self, now: float) -> None:
        filter_name = self._config().crop.filter_name
        while self._inbox:
            kind, data = self._inbox.popleft()
            if kind == CONNECTION_LOST:
                self._needs_sync = self._needs_measure = self._adopt = True
            elif kind == "InputSettingsChanged":
                self._needs_measure = True
                self._size_hint = _resolution(data.get("inputSettings"))
            elif kind in FILTER_EVENTS:
                self._needs_sync = self._needs_measure = self._adopt = True
            elif kind == "SourceFilterSettingsChanged" and data.get("filterName") == filter_name:
                settings = data.get("filterSettings")
                crop = PixelCrop.from_filter_settings(
                    settings if isinstance(settings, dict) else {}
                )
                if crop != self._written and now - self._last_write_at >= WRITE_PROTECTION_SECONDS:
                    # Someone moved the crop in OBS: that is where the window is now.
                    self._needs_sync = self._adopt = True

    def _publish(self, config: CameraConfig, now: float) -> None:
        frame = _frame(self._source_size, config) if self._source_size else None
        with self._lock:
            moving = self._motion.moving
        status, reason = self._health
        self._publisher.publish(
            now,
            moving=moving,
            crop=crop_state(frame, self._window, config) if frame and self._window else None,
            status=status,
            reason=reason,
            last_error=self._last_error,
        )


def _frame(size: tuple[int, int], config: CameraConfig) -> CropFrame:
    return CropFrame(size[0], size[1], config.crop.aspect_ratio, config.crop.min_window)


def _resolution(settings: object) -> tuple[int, int] | None:
    """A video capture device's `resolution` setting ("1920x1080"), if it names one."""
    if not isinstance(settings, dict):
        return None
    width, _, height = str(settings.get("resolution", "")).partition("x")
    if width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
        return int(width), int(height)
    return None
