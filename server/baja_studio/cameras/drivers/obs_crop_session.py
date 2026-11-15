"""One OBS crop camera's conversation with OBS: what the driver's worker does each step.

Each `step`:

1. reads the filter (`obs_crop_sync.read_filter`) when needed (start, OBS
   back, a filter or source event, an edit made in OBS) and takes the crop
   OBS has as the window whenever it differs from what the studio last
   wrote, so connecting never moves the picture;
2. measures the source when its size may have changed (`obs_crop_size.py`);
   motion waits while the size is not known or not confirmed;
3. applies the pending jump and the held velocity (`obs_crop_motion.py`),
   and sends `SetSourceFilterSettings` only when the pixel edges changed.
   Settings edits (a narrower `min_window`, another aspect) apply at the next
   move, never by themselves; only a new source size re-expresses the crop.

Echoes of its own writes are told from edits made in OBS by `obs_crop_edits.py`.
Everything here runs on the driver's worker thread.
"""

from typing import Any

from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.agents.contract import Status
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_window import (
    CropFrame,
    CropSpeeds,
    CropWindow,
    PixelCrop,
    WindowTarget,
)
from baja_studio.cameras.drivers.obs_crop_edits import EditWatch
from baja_studio.cameras.drivers.obs_crop_motion import CropCommands
from baja_studio.cameras.drivers.obs_crop_size import Measured, SourceSize
from baja_studio.cameras.drivers.obs_crop_sync import (
    CropUnavailableError,
    FilterReading,
    measure_source,
    read_filter,
)
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST

IDLE_SECONDS = 1.0
MEASURE_RETRY_SECONDS = 0.1
# Events that mean "read the filter again, and measure".
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
Key = tuple[str, str]  # (source, filter name)


def crop_frame(size: tuple[int, int], config: CameraConfig) -> CropFrame:
    """The frame windows are cut from, for a source size and the camera's settings."""
    return CropFrame(size[0], size[1], config.crop.aspect_ratio, config.crop.min_window)


class CropSession:
    """What one OBS crop camera knows about OBS, and the steps that keep it right."""

    def __init__(self, commands: CropCommands) -> None:
        """Nothing known yet: read and measure first."""
        self._commands = commands
        self.needs_sync = True
        self._rewrite = False
        self.sizes = SourceSize()
        self.edits = EditWatch()
        self._reading: FilterReading | None = None
        self.window: CropWindow | None = None
        self.health = (Status.OFFLINE, "starting")
        self.last_error = ""

    def step(self, client: ObsRequestsMixin, key: Key, config: CameraConfig, now: float) -> float:
        """One look at OBS and one motion step; returns how long to sleep.

        Raises `CropUnavailableError` or `ObsError`; the driver turns them into health.
        """
        if self.needs_sync or self.edits.read_again_due(now):
            self._sync(client, key)
        if self._commands.take_reset():
            self._reset(client, key, now)
        if self._rewrite:
            self._write(client, key, config, now)  # the crop, re-expressed for a new size
        settled = not self.sizes.due(now, self._commands.at_rest) or self._measure(client, key, now)
        if not settled or self.sizes.needed:
            self._commands.pause(now)  # motion waits, and does not catch up later
            return MEASURE_RETRY_SECONDS
        assert self.sizes.size is not None and self._reading is not None
        frame = crop_frame(self.sizes.size, config)
        if self.window is None:
            assert self.edits.written is not None
            self.window = frame.window_of(self.edits.written)
        window = self._commanded_window(frame, config, now)
        if window is not None and window != self.window:
            self.window = window
            self._write(client, key, config, now)
        self.health = (Status.OK, "")
        if not self._reading.enabled:
            self.health = (
                Status.DEGRADED,
                f"filter '{key[1]}' is switched off in OBS (its eye): the picture is not cropped",
            )
        if self._commands.moving:
            return 1.0 / config.crop.update_hz
        read_again = self.edits.read_again_at()
        return IDLE_SECONDS if read_again is None else max(0.0, min(IDLE_SECONDS, read_again - now))

    def unavailable(self, status: Status, reason: str, lost: bool = False) -> None:
        """OBS refused or went away; `lost`: read and measure everything once it is back."""
        self.needs_sync = True
        if lost:
            self.sizes.unknown()
        self._commands.forget()
        self.health = (status, reason)

    def source_changed(self) -> None:
        """Another source or filter name in the settings: start afresh."""
        self.needs_sync = True
        self.sizes.unknown()

    def event(self, kind: str, data: dict[str, Any], filter_name: str, now: float) -> None:
        """An OBS event about this camera's source (or the connection dropping)."""
        if kind == CONNECTION_LOST:
            self.needs_sync = True
            self.sizes.unknown()
        elif kind == "InputSettingsChanged":
            if self.sizes.settings_changed(_resolution(data.get("inputSettings")), now):
                self._rewrite = True
        elif kind in FILTER_EVENTS:
            self.needs_sync = True
            self.sizes.unknown()
            self.sizes.touched(now)
        elif kind == "SourceFilterSettingsChanged" and data.get("filterName") == filter_name:
            settings = data.get("filterSettings")
            crop = PixelCrop.from_filter_settings(settings if isinstance(settings, dict) else {})
            self.sizes.touched(now)
            if self.edits.settings_event(crop, now):
                self.needs_sync = True

    # --- inside a step ------------------------------------------------------------------

    def _sync(self, client: ObsRequestsMixin, key: Key) -> None:
        """Read the filter; a crop other than the one last written becomes the window."""
        reading = read_filter(client, *key)
        if reading.crop != self.edits.written:
            self.window = None  # taken from OBS once the size is known
            self.edits.written = reading.crop
        self._reading, self.needs_sync = reading, False

    def _measure(self, client: ObsRequestsMixin, key: Key, now: float) -> bool:
        """Measure the source; False while its size is not settled (try again shortly)."""
        reading = read_filter(client, *key)
        try:
            size = measure_source(client, *key, reading)
        except CropUnavailableError:
            if self.sizes.waiting_for_announced(now):
                return False  # the device is still reopening at its new resolution
            raise
        if size is None:
            return False
        outcome = self.sizes.measured(size, now)
        if outcome == Measured.NOT_YET:
            return False
        self._reading = reading
        if reading.crop != self.edits.written:
            self.edits.written, self.window = reading.crop, None
        if outcome == Measured.CHANGED and self.window is not None:
            self._rewrite = True
        return True

    def _write(self, client: ObsRequestsMixin, key: Key, config: CameraConfig, now: float) -> None:
        """Send the window's edges, if they differ from what OBS has."""
        self._rewrite = False
        if self.window is None or self.sizes.size is None:
            return
        pixels = crop_frame(self.sizes.size, config).pixels(self.window)
        if pixels != self.edits.written:
            client.set_source_filter_settings(*key, pixels.filter_settings())
            self.edits.wrote(pixels, now)
            self.sizes.touched(now)

    def _reset(self, client: ObsRequestsMixin, key: Key, now: float) -> None:
        """Show the whole picture: nothing cut. Needs no size, so it also mends a crop
        left larger than the picture by a lower resolution; then measure again."""
        nothing = PixelCrop(0, 0, 0, 0)
        client.set_source_filter_settings(*key, nothing.filter_settings())
        self.edits.wrote(nothing, now)
        self.sizes.touched(now)
        self.sizes.unknown()
        self.window = None  # the whole picture, once measured

    def _commanded_window(
        self, frame: CropFrame, config: CameraConfig, now: float
    ) -> CropWindow | None:
        """The window after this step's jump and motion, or None if nothing moved it."""
        assert self.window is not None
        jump, step = self._commands.take(now)
        window = self.window
        if isinstance(jump, WindowTarget):
            if jump.whole:
                window = frame.whole()
            else:
                window = frame.centred_on(window, jump.centre_x, jump.centre_y)
        elif jump is not None:
            stored = config.crop.window(jump)
            if stored is None:
                self.last_error = f"preset {jump + 1} is not stored"
                jump = None
            else:
                window = CropWindow(stored.centre_x, stored.centre_y, stored.width)
        if step.seconds > 0:
            crop = config.crop
            speeds = CropSpeeds(crop.pan_speed, crop.tilt_speed, crop.zoom_speed)
            window = frame.drive(
                window, step.velocity, step.seconds, speeds, crop.speed_follows_zoom
            )
        if jump is None and step.seconds <= 0:
            return None
        return frame.clamp(window)


def _resolution(settings: object) -> tuple[int, int] | None:
    """A video capture device's `resolution` setting ("1920x1080"), if it names one."""
    if not isinstance(settings, dict):
        return None
    width, _, height = str(settings.get("resolution", "")).partition("x")
    if width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
        return int(width), int(height)
    return None
