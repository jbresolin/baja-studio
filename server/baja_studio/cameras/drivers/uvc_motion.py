"""The arithmetic of the `uvc` driver (the EMEET Pixy): velocities in, absolute positions out.

UVC on the Pixy only takes absolute pan, tilt and zoom. The arbiter speaks
velocities, so this integrates them into a target position, clamped to the
device's ranges, and says which axes changed by at least one device step
since they were last written. A plain class told `now` on every call, as
the conventions ask for time-dependent protocol logic.

Position readback on the Pixy is the last value written through UVC, never
the real position, so the only position there is to report is the commanded
one (`written`).
"""

from dataclasses import dataclass

from baja_studio.agents.contract import Position
from baja_studio.cameras.driver import AT_REST, Velocity

AXES = ("pan", "tilt", "zoom")
# Deadlines are float sums; without slack, six 0.05 s steps fall short of 0.3 s.
TIME_TOLERANCE_SECONDS = 1e-6


@dataclass(frozen=True)
class AxisRange:
    """One axis as the device reports it: whole device units."""

    min: float
    max: float
    step: float

    def clamp(self, value: float) -> float:
        """`value` kept inside the range."""
        return min(self.max, max(self.min, value))

    def device_value(self, value: float) -> int:
        """`value` clamped and put on the step, as a whole number to write."""
        steps = round((self.clamp(value) - self.min) / self.step)
        return round(self.clamp(self.min + steps * self.step))


@dataclass(frozen=True)
class TopSpeeds:
    """Device units per second at velocity 1.0 (degrees for the Pixy's pan and tilt)."""

    pan: float
    tilt: float
    zoom: float


class PtzIntegrator:
    """Velocity in, the axes to write out; no I/O, no clock of its own."""

    def __init__(self, speeds: TopSpeeds) -> None:
        """Not connected: no ranges, nothing to write."""
        self._speeds = speeds
        self.ranges: dict[str, AxisRange] = {}
        self.target: dict[str, float] = {}
        self.written: dict[str, int] = {}
        self._velocity = AT_REST
        self._expires_at = 0.0
        self._last: float | None = None

    @property
    def connected(self) -> bool:
        """True once the device's ranges are known."""
        return bool(self.ranges)

    def connect(self, ranges: dict[str, AxisRange], readback: dict[str, float]) -> None:
        """The device answered: its ranges, and what it reads back (the last value written)."""
        self.ranges = dict(ranges)
        self.target = {axis: ranges[axis].clamp(readback[axis]) for axis in ranges}
        self.written = {axis: ranges[axis].device_value(readback[axis]) for axis in ranges}
        self._velocity, self._last = AT_REST, None

    def disconnect(self) -> None:
        """The device went away: forget its ranges and stop integrating."""
        self.ranges, self.target, self.written = {}, {}, {}
        self._velocity, self._last = AT_REST, None

    def move(self, velocity: Velocity, ttl_seconds: float, now: float) -> None:
        """Integrate `velocity` from now until `ttl_seconds` pass without another `move`."""
        self._integrate(now)
        self._velocity, self._expires_at, self._last = velocity, now + ttl_seconds, now

    def stop(self, now: float) -> None:
        """Stop integrating. Nothing needs sending: the Pixy holds the last position written."""
        self._integrate(now)
        self._velocity = AT_REST

    def jump(self, position: dict[str, float], now: float) -> None:
        """Go to an absolute position (a preset): any velocity ends."""
        self.stop(now)
        for axis, value in position.items():
            if axis in self.ranges:
                self.target[axis] = self.ranges[axis].clamp(value)

    def moving(self, now: float) -> bool:
        """True while a velocity is being integrated."""
        return not self._velocity.is_zero and now < self._expires_at - TIME_TOLERANCE_SECONDS

    def due_writes(self, now: float) -> dict[str, int]:
        """Bring the target up to `now`; the axes whose device value differs from the last write."""
        self._integrate(now)
        due = {}
        for axis, axis_range in self.ranges.items():
            value = axis_range.device_value(self.target[axis])
            if value != self.written.get(axis):
                due[axis] = value
        return due

    def wrote(self, values: dict[str, int]) -> None:
        """These axes were written (the ones the device refused are left out)."""
        self.written.update(values)

    def commanded(self) -> dict[str, float] | None:
        """The last position written, in device units; None until connected."""
        if not self.connected:
            return None
        return {axis: float(value) for axis, value in self.written.items()}

    def position(self) -> Position | None:
        """The commanded position: pan and tilt in device units, zoom normalised 0..1."""
        if not {"pan", "tilt"} <= self.written.keys():
            return None
        zoom = 0.0
        zoom_range = self.ranges.get("zoom")
        if zoom_range is not None and zoom_range.max > zoom_range.min:
            zoom = (self.written["zoom"] - zoom_range.min) / (zoom_range.max - zoom_range.min)
        return Position(pan=self.written["pan"], tilt=self.written["tilt"], zoom=zoom)

    def _integrate(self, now: float) -> None:
        last, self._last = self._last, now
        if last is None or self._velocity.is_zero:
            return
        seconds = max(0.0, min(now, self._expires_at) - last)
        for axis, velocity in zip(
            AXES, (self._velocity.pan, self._velocity.tilt, self._velocity.zoom), strict=True
        ):
            axis_range = self.ranges.get(axis)
            if axis_range is not None and velocity:
                speed = getattr(self._speeds, axis)
                self.target[axis] = axis_range.clamp(self.target[axis] + velocity * speed * seconds)
        if now >= self._expires_at - TIME_TOLERANCE_SECONDS:
            self._velocity = AT_REST
