"""The `uvc` driver: a USB PTZ webcam (the EMEET Pixy) moved through UVC absolute positions.

The arbiter's velocities are integrated about 20 times a second into a
target (`uvc_motion.PtzIntegrator`), clamped to the ranges read from the
device, and only axes that changed are written, through the UVC child.
Stop means: stop integrating; there is nothing to send, because the Pixy
holds the last position written.

What the Pixy cannot tell, the driver does not pretend to know: its readback
is the last value written through UVC, not where the camera points (a hand,
its AI tracking or the EMEET app move it without UVC noticing). So the
published position is the commanded one, flagged `position_is_commanded`,
and `has_position_feedback` is false. Presets are kept in the camera's
settings (`stored_positions`: pan, tilt and zoom), because the Pixy's own
preset slots do not keep zoom.
"""

import threading
from collections.abc import Callable
from typing import Any, Protocol

from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.cameras.drivers.uvc_motion import AXES, AxisRange, PtzIntegrator, TopSpeeds
from baja_studio.cameras.drivers.visca_packets import PRESET_MAX
from baja_studio.cameras.motion.arbiter import MotionRefusedError
from baja_studio.core.childproc import ChildRequestError
from baja_studio.core.worker import DeviceOfflineError, HealthState, WorkerContext, WorkerSpec
from baja_studio.imaging.config import StoredPosition
from baja_studio.imaging.usb_devices import Located

# Device units per second at full stick. The Pixy's pan and tilt units are
# degrees (pixy_ptz); these are cautious guesses for its motor, which the
# per-camera speed scales can only lower. The bench sheet asks about them.
PIXY_TOP_SPEEDS = TopSpeeds(pan=60.0, tilt=45.0, zoom=25.0)
UPDATE_INTERVAL_SECONDS = 0.05  # 20 Hz while moving
IDLE_INTERVAL_SECONDS = 0.5  # how often an idle driver looks for its device
_HEALTH = {Status.OK: HealthState.OK, Status.DEGRADED: HealthState.DEGRADED}


class UsbPtzAccess(Protocol):
    """What the driver needs of the imaging service (which owns the UVC link)."""

    def locate_camera(self, camera_id: str) -> Located:
        """Where the camera's USB device is now, or why it is not found."""

    def read_controls(self, path: str, names: list[str]) -> dict[str, Any]:
        """The UVC child's `read_controls`."""

    def write_controls(self, path: str, controls: dict[str, Any]) -> dict[str, str | None]:
        """The UVC child's `write_controls`: per control, None or the refusal."""


class UvcPtzDriver(CameraDriver):
    """One USB PTZ camera owned by the studio."""

    def __init__(
        self,
        host: DriverHost,
        usb: UsbPtzAccess,
        stored_positions: Callable[[], list[StoredPosition]],
        save_position: Callable[[StoredPosition], None],
        speeds: TopSpeeds = PIXY_TOP_SPEEDS,
    ) -> None:
        """`stored_positions` reads the camera's presets live; `save_position` saves one."""
        self._host = host
        self._usb = usb
        self._stored_positions = stored_positions
        self._save_position = save_position
        self.worker_name = f"{host.worker_prefix}:uvc"
        self._lock = threading.Lock()
        self._motion = PtzIntegrator(speeds)
        self._path: str | None = None
        self._health = (Status.OFFLINE, "starting")
        self._last_error = ""
        self._worker: WorkerContext | None = None
        self._published: dict[str, Any] = {}

    # --- CameraDriver -----------------------------------------------------------------

    def connect(self) -> None:
        """Start the worker; it reads the ranges and writes nothing until asked to move."""
        self._publish()
        self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop the worker (the camera stays where it is)."""
        self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Integrate `velocity` for `ttl_ms`; the worker writes the positions."""
        with self._lock:
            self._motion.move(velocity, ttl_ms / 1000, self._host.clock.monotonic())
        self._wake()

    def stop(self) -> None:
        """Stop integrating; the camera keeps the last position written."""
        with self._lock:
            self._motion.stop(self._host.clock.monotonic())
        self._wake()

    def recall_preset(self, n: int) -> None:
        """Jump to stored preset `n` (pan, tilt and zoom)."""
        stored = next((p for p in self._stored_positions() if p.n == n), None)
        if stored is None:
            raise MotionRefusedError(f"preset {n + 1} is not stored on this camera yet")
        with self._lock:
            if not self._motion.connected:
                raise MotionRefusedError("the camera is not connected")
            position = {"pan": stored.pan, "tilt": stored.tilt, "zoom": stored.zoom}
            self._motion.jump(position, self._host.clock.monotonic())
        self._wake()

    def store_preset(self, n: int) -> None:
        """Save the commanded position as preset `n` in the camera's settings."""
        with self._lock:
            commanded = self._motion.commanded()
        if commanded is None:
            raise MotionRefusedError("the camera is not connected")
        self._save_position(
            StoredPosition(
                n=n,
                pan=commanded.get("pan", 0.0),
                tilt=commanded.get("tilt", 0.0),
                zoom=commanded.get("zoom", 0.0),
            )
        )

    def position(self) -> Position | None:
        """The commanded position (all the Pixy can tell)."""
        with self._lock:
            return self._motion.position()

    def capabilities(self) -> DriverCapabilities:
        """A PTZ with zoom; no real position feedback, no auto, presets 0-127 in settings."""
        return DriverCapabilities(
            kind=CameraKind.PTZ,
            has_position_feedback=False,
            has_zoom=True,
            supports_auto=False,
            preset_max=PRESET_MAX,
        )

    def health(self) -> Status:
        """offline while the device is away; degraded after a refused write."""
        return self._health[0]

    # --- the worker -----------------------------------------------------------------

    def _wake(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.wake()

    def _run(self, worker: WorkerContext) -> None:
        self._worker = worker
        try:
            while not worker.should_stop:
                pause = self._step()
                self._publish()
                status, reason = self._health
                worker.report_health(_HEALTH.get(status, HealthState.OFFLINE), reason)
                worker.sleep(pause)
        finally:
            self._worker = None

    def _step(self) -> float:
        """Find the device, connect to it, write what is due; returns how long to sleep."""
        located = self._usb.locate_camera(self._host.camera_id)
        if located.device is None:
            self._lose(located.problem)
            return IDLE_INTERVAL_SECONDS
        path = located.device.path
        try:
            if path != self._path:
                self._connect(path)
            self._write_due(path)
        except (DeviceOfflineError, ChildRequestError) as error:
            self._lose(f"USB camera not answering: {error}")
            return IDLE_INTERVAL_SECONDS
        with self._lock:
            moving = self._motion.moving(self._host.clock.monotonic())
        return UPDATE_INTERVAL_SECONDS if moving else IDLE_INTERVAL_SECONDS

    def _connect(self, path: str) -> None:
        answer = self._usb.read_controls(path, list(AXES))
        controls = answer.get("controls", {})
        missing = [axis for axis in ("pan", "tilt") if axis not in controls]
        if missing:
            raise ChildRequestError(f"the device has no {' or '.join(missing)} control")
        ranges = {
            axis: AxisRange(float(data["min"]), float(data["max"]), float(data.get("step") or 1))
            for axis, data in controls.items()
        }
        readback = {axis: float(data["value"]) for axis, data in controls.items()}
        with self._lock:
            self._motion.connect(ranges, readback)
            self._path = path
        self._health = (Status.OK, "")
        self._host.logger.info("camera %s: USB PTZ connected at %s", self._host.camera_id, path)

    def _write_due(self, path: str) -> None:
        with self._lock:
            due = self._motion.due_writes(self._host.clock.monotonic())
        if not due:
            return
        controls = {axis: {"value": value, "auto": False} for axis, value in due.items()}
        errors = self._usb.write_controls(path, controls)
        refused = {axis: error for axis, error in errors.items() if error}
        with self._lock:
            self._motion.wrote({axis: value for axis, value in due.items() if axis not in refused})
        if refused:
            self._last_error = "; ".join(f"{axis}: {error}" for axis, error in refused.items())
            self._health = (Status.DEGRADED, f"the camera refused {', '.join(refused)}")
        else:
            self._health = (Status.OK, "")

    def _lose(self, reason: str) -> None:
        with self._lock:
            self._motion.disconnect()
            self._path = None
        self._health = (Status.OFFLINE, reason)

    def _publish(self) -> None:
        """Write the CameraState fields that changed."""
        with self._lock:
            position = self._motion.position()
            moving = self._motion.moving(self._host.clock.monotonic())
        status, reason = self._health
        fields: dict[str, Any] = {
            "position": position.model_dump() if position else None,
            "position_is_commanded": True,
            "moving": moving,
            "last_error": self._last_error,
            "status_reason": reason,
            "status": status.value,  # last, so a page that sees "ok" sees the position too
        }
        for key, value in fields.items():
            if self._published.get(key, ...) != value:
                self._published[key] = value
                self._host.publish(key, value)
