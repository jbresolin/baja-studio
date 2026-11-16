"""One camera the node serves, as the agent contract sees it: a `kind=crop` camera (ADR 0023).

Whichever video mode the camera is in, a `CameraDriver` moves its window:
the studio's own `ObsCropDriver` in `obs` mode, `PipelineCropDriver` in
`node` mode (`backends.py` builds them). This class puts the contract's
rules around that driver:

- `/move` holds a velocity for `ttl_ms` (the driver's dead-man ends it);
  all zeros is a stop; `/stop` is never refused, even with the device away;
- `PUT /crop` jumps the window there (the narrowest window allowed is
  widened, and the answer is what the driver applied); `GET /crop` and
  `/position` are what the driver last published;
- modes: `manual` and `hold` only; `auto` is `400 not_supported`, since the
  node has no tracker (`supports_auto: false`, contract §7);
- the status is the driver's health; motion commands to a camera whose
  driver is `offline` (OBS away, the pipeline failed) are `503 device_offline`;
- `device_offline` / `device_online` and `mode_changed` events.

Swapping the driver (another video mode, or new video settings) keeps the
camera's mode and its last command, so the studio sees the same camera.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baja_studio.agents.contract import (
    CAMERA_CAPABILITY_SNAPSHOT,
    CameraInfo,
    CameraKind,
    CameraLimits,
    CameraState,
    CommandReply,
    CropRect,
    ErrorCode,
    EventName,
    Mode,
    ModeReply,
    ModeRequest,
    MoveRequest,
    Position,
    Status,
    TrackerState,
    TrackerStatus,
)
from baja_studio.agents.server import CameraCommandError, checked_profile
from baja_studio.cameras.crop_window import CropFrame, CropWindow, WindowTarget
from baja_studio.cameras.driver import CameraDriver, Velocity
from baja_studio.core.clock import Clock
from baja_studio.node.settings import NodeCameraSettings, VideoMode
from baja_studio.node.snapshots import Snapshot

# How long `PUT /crop` waits for the driver to apply the window before answering.
APPLY_WAIT_SECONDS = 1.0
WINDOW_TOLERANCE = 1e-4
OUTPUT_ASPECT = 16 / 9
WINDOW_KEYS = ("x", "y", "w", "h")


@dataclass
class Backend:
    """What one video mode gives a camera: the driver, its snapshots, and the page's details."""

    mode: str  # "node" or "obs"
    driver: CameraDriver
    snapshot: Callable[[int], Snapshot]
    details: Callable[[], dict[str, Any]]


class NodeCamera:
    """The contract's camera around whichever driver the camera's video mode uses."""

    def __init__(
        self,
        settings: Callable[[], NodeCameraSettings],
        clock: Clock,
        image_controls: Callable[[], list[str]],
    ) -> None:
        """`settings` is read on every call; `image_controls()` the names the device has."""
        self._settings = settings
        self._clock = clock
        self._image_controls = image_controls
        self._lock = threading.Lock()
        self._published: dict[str, Any] = {}
        self._crop_changed = threading.Event()
        self.backend: Backend | None = None
        self.mode = Mode.MANUAL
        self.profile: dict[str, Any] = {}
        self.last_command_source: str | None = None
        self.last_command_ts: str | None = None
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._reported_status: Status | None = None
        # Why there is no backend (e.g. no OBS source chosen), while there is none.
        self._problem = ""

    @property
    def id(self) -> str:
        """The camera's id on this agent."""
        return self._settings().id

    # --- the driver's side ------------------------------------------------------------------

    def use(self, backend: Backend | None, problem: str = "") -> None:
        """Drive the camera through `backend` from now on.

        None while a backend is being swapped, or when none can run (`problem` says why).
        """
        with self._lock:
            self.backend = backend
            self._published = {}
            self._problem = problem

    def published(self, backend: Backend, key: str, value: Any) -> None:
        """A driver published one of its fields; dropped if that driver is no longer in use."""
        with self._lock:
            if self.backend is not backend:
                return
            self._published[key] = value
        if key == "crop":
            self._crop_changed.set()

    def field(self, key: str) -> Any:
        """A field the current driver last published (None if it has not)."""
        with self._lock:
            return self._published.get(key)

    # --- what the agent reports -------------------------------------------------------------

    def info(self) -> CameraInfo:
        """This camera's entry in `/agent/info`."""
        camera = self._settings()
        return CameraInfo(
            id=camera.id,
            label=camera.label or camera.id,
            kind=CameraKind.CROP,
            driver="node-pipeline" if camera.mode == VideoMode.NODE else "node-obs",
            has_position_feedback=True,
            has_zoom=True,
            supports_auto=False,
            image_controls=self._image_controls(),
            presets=[],
            limits=CameraLimits(
                pan_speed_max=camera.pan_speed,
                tilt_speed_max=camera.tilt_speed,
                zoom_speed_max=camera.zoom_speed,
                preset_max=None,
            ),
            capabilities=[CAMERA_CAPABILITY_SNAPSHOT],
        )

    def status(self) -> tuple[Status, str]:
        """The driver's health and its reason; offline while there is no driver."""
        with self._lock:
            if self.backend is None:
                return Status.OFFLINE, self._problem or "starting"
            value = self._published.get("status")
            reason = str(self._published.get("status_reason") or "")
        if value is None:
            return Status.OFFLINE, "starting"
        return Status(value), reason

    def position(self) -> Position | None:
        """The window's centre and zoom, once the driver knows the window."""
        position = self.field("position")
        return None if position is None else Position.model_validate(position)

    def state(self) -> CameraState:
        """This camera's entry in a state push."""
        return CameraState(
            status=self.status()[0],
            mode=self.mode,
            tracker=TrackerState(state=TrackerStatus.IDLE),
            position=self.position(),
            moving=bool(self.field("moving")),
            last_command_source=self.last_command_source,
            last_command_ts=self.last_command_ts,
        )

    def check_status(self) -> None:
        """Called every push cycle: a device going offline or coming back is an event."""
        status = self.status()[0]
        previous, self._reported_status = self._reported_status, status
        if previous is None or (previous == Status.OFFLINE) == (status == Status.OFFLINE):
            return
        offline = status == Status.OFFLINE
        self._emit(EventName.DEVICE_OFFLINE if offline else EventName.DEVICE_ONLINE, {})

    # --- motion -----------------------------------------------------------------------------

    def move(self, request: MoveRequest, source: str, now_iso: str) -> CommandReply:
        """Hold the velocity for `ttl_ms`; all zeros is a stop."""
        if request.pan == request.tilt == request.zoom == 0:
            return self.stop(source, now_iso)
        driver = self._driver_online()
        driver.move(Velocity(request.pan, request.tilt, request.zoom), request.ttl_ms)
        self._studio_motion(source, now_iso)
        return CommandReply(accepted=True, mode=self.mode)

    def stop(self, source: str, now_iso: str) -> CommandReply:
        """Stop the window at once; never refused for the device being away."""
        backend = self.backend
        if backend is not None:
            backend.driver.stop()
        self._studio_motion(source, now_iso)
        return CommandReply(accepted=True, mode=self.mode)

    def crop(self) -> CropRect:
        """Where the window is now."""
        window = self.field("crop")
        if not isinstance(window, dict):
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, self._unknown_window())
        x, y, w, h = (_inside(window[key]) for key in WINDOW_KEYS)
        # Published values are rounded to 6 decimals: keep x + w inside the frame anyway.
        return CropRect(x=min(x, 1.0 - w), y=min(y, 1.0 - h), w=w, h=h)

    def set_crop(self, rect: CropRect, source: str, now_iso: str) -> CropRect:
        """Jump the window to `rect` (its centre and width), and answer what was applied."""
        driver = self._driver_online()
        window = self.field("crop")
        if not isinstance(window, dict) or not window.get("source_width"):
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, self._unknown_window())
        frame = CropFrame(
            int(window["source_width"]),
            int(window["source_height"]),
            OUTPUT_ASPECT,
            self._settings().min_window,
        )
        centre_x, centre_y = rect.x + rect.w / 2, rect.y + rect.h / 2
        wanted = frame.rect(frame.clamp(CropWindow(centre_x, centre_y, rect.w)))
        self._crop_changed.clear()
        place = getattr(driver, "place_window", None)
        assert callable(place), "node drivers can place their window"
        place(WindowTarget(centre_x, centre_y, width=rect.w))
        self._studio_motion(source, now_iso)
        self._wait_for_window(wanted)
        return self.crop()

    # --- modes and snapshots ----------------------------------------------------------------

    def mode_reply(self) -> ModeReply:
        """`GET /mode`."""
        return ModeReply(mode=self.mode, profile=self.profile)

    def set_mode(self, request: ModeRequest) -> ModeReply:
        """`PUT /mode`: manual or hold; there is no tracker, so auto is not supported."""
        if request.mode == Mode.AUTO:
            raise CameraCommandError(
                ErrorCode.NOT_SUPPORTED, f"{self.id} has no tracker: it cannot be put in auto"
            )
        if request.profile is not None:
            self.profile = checked_profile(request.profile)
        if request.mode != self.mode:
            previous, self.mode = self.mode, request.mode
            detail = {"from": previous, "to": self.mode, "reason": "requested by the studio"}
            self._emit(EventName.MODE_CHANGED, detail)
        return self.mode_reply()

    def snapshot(self, width: int) -> Snapshot:
        """The camera's picture for `GET /snapshot`; `no_snapshot` while there is none."""
        backend = self.backend
        if backend is None:
            raise CameraCommandError(ErrorCode.NO_SNAPSHOT, "the camera is starting")
        return backend.snapshot(width)

    # --- internals ----------------------------------------------------------------------------

    def _driver_online(self) -> CameraDriver:
        backend = self.backend
        status, reason = self.status()
        if backend is None or status == Status.OFFLINE:
            raise CameraCommandError(
                ErrorCode.DEVICE_OFFLINE, f"{self.id} is offline: {reason or 'starting'}"
            )
        return backend.driver

    def _unknown_window(self) -> str:
        reason = self.status()[1]
        return f"{self.id}'s window is not known yet" + (f": {reason}" if reason else "")

    def _wait_for_window(self, wanted: dict[str, float]) -> None:
        """Wait (at most a second) until the driver has published the wanted window."""
        deadline = self._clock.monotonic() + APPLY_WAIT_SECONDS
        while True:
            window = self.field("crop")
            if isinstance(window, dict) and all(
                abs(float(window[key]) - wanted[key]) <= WINDOW_TOLERANCE for key in WINDOW_KEYS
            ):
                return
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                return
            self._clock.wait(self._crop_changed, remaining)
            self._crop_changed.clear()

    def _studio_motion(self, source: str, now_iso: str) -> None:
        self.last_command_source, self.last_command_ts = source, now_iso

    def _emit(self, event: EventName, detail: dict[str, Any]) -> None:
        with self._lock:
            self._events.append((event.value, detail))

    def take_events(self) -> list[tuple[str, dict[str, Any]]]:
        """The events produced since the last call (each is sent once)."""
        with self._lock:
            events, self._events = self._events, []
        return events


def _inside(value: Any) -> float:
    """A published fraction, kept inside 0..1 (rounding can leave 1.0000001)."""
    return min(1.0, max(0.0, float(value)))
