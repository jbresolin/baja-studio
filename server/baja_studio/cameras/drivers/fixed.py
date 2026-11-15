"""The `none` driver: a camera with no motion of its own (a fixed S600 webcam).

It lets such a camera live in the registry, for its image settings and its
preview, without pretending to be a PTZ: `kind` is `fixed`, there are no
presets, and the cameras commands refuse moves before they reach it (moves
that still arrive, from a joystick addressed to it, do nothing).

With a USB device its status follows the device: `ok` while it is plugged
in, `offline` with the reason while it is not, checked twice a second by a
small `camera:<id>:usb` worker so the home card lists it like any device.
Without one it is simply `ok`.
"""

from collections.abc import Callable

from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.core.worker import HealthState, WorkerContext, WorkerSpec
from baja_studio.imaging.usb_devices import Located

PRESENCE_INTERVAL_SECONDS = 0.5


class FixedCameraDriver(CameraDriver):
    """A camera that never moves."""

    def __init__(self, host: DriverHost, locate: Callable[[], Located] | None) -> None:
        """`locate` finds the camera's USB device; None for a camera without one."""
        self._host = host
        self._locate = locate
        self.worker_name = f"{host.worker_prefix}:usb"
        self._health = (Status.OK, "") if locate is None else (Status.OFFLINE, "starting")
        self._published: tuple[str, str] | None = None

    def connect(self) -> None:
        """Publish the status; with a USB device, start watching it."""
        self._publish()
        if self._locate is not None:
            self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop watching the device."""
        if self._locate is not None:
            self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Nothing to move."""

    def stop(self) -> None:
        """Nothing to stop."""

    def recall_preset(self, n: int) -> None:
        """Never called: the arbiter refuses presets on a camera without `preset_max`."""

    def store_preset(self, n: int) -> None:
        """Never called, as for `recall_preset`."""

    def position(self) -> Position | None:
        """A fixed camera points where it was mounted."""
        return None

    def capabilities(self) -> DriverCapabilities:
        """Fixed: no motion, no zoom, no presets."""
        return DriverCapabilities(
            kind=CameraKind.FIXED,
            has_position_feedback=False,
            has_zoom=False,
            supports_auto=False,
            preset_max=None,
        )

    def health(self) -> Status:
        """The USB device's presence (always ok without one)."""
        return self._health[0]

    def _run(self, worker: WorkerContext) -> None:
        assert self._locate is not None
        while not worker.should_stop:
            located = self._locate()
            if located.device is None:
                self._health = (Status.OFFLINE, located.problem)
                worker.report_health(HealthState.OFFLINE, located.problem)
            else:
                self._health = (Status.OK, "")
                worker.mark_online()
                worker.report_health(HealthState.OK)
            self._publish()
            worker.sleep(PRESENCE_INTERVAL_SECONDS)

    def _publish(self) -> None:
        status, reason = self._health
        if self._published != (status.value, reason):
            self._published = (status.value, reason)
            self._host.publish("status_reason", reason)
            self._host.publish("status", status.value)
