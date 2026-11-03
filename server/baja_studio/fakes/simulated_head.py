"""Where a simulated camera points, driven by velocities: a PTZ head or a crop window.

Pure: the position is worked out from the velocity when asked, up to the
moment the velocity expires, so there are no timers to go wrong.
"""

from dataclasses import dataclass, field

from baja_studio.agents.contract import CameraKind, Position
from baja_studio.fakes.simulated_common import PAN_RANGE, PTZ_LIMITS, TILT_RANGE, ZOOM_RANGE, clamp
from baja_studio.fakes.simulated_crop import CropWindow

Velocity = tuple[float, float, float]


@dataclass
class SimulatedHead:
    """The moving part of one camera; `kind` decides whether a head or a window moves."""

    kind: CameraKind
    crop_window: CropWindow = field(default_factory=CropWindow)
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    velocity: Velocity = (0.0, 0.0, 0.0)
    velocity_until: float = 0.0
    integrated_at: float = 0.0

    def drive(self, now: float, velocity: Velocity, until: float) -> None:
        """Move at `velocity` (normalised) from `now` until `until`."""
        self.integrate(now)
        self.velocity, self.velocity_until = velocity, until

    def halt(self, now: float) -> None:
        """Stop at once, keeping the position reached so far."""
        self.integrate(now)
        self.velocity_until = now

    def moving(self, now: float) -> bool:
        """True while a velocity is still running."""
        return now < self.velocity_until and any(self.velocity)

    def pan_tilt_zoom(self) -> tuple[float, float, float]:
        """The PTZ position, to store as a preset."""
        return self.pan, self.tilt, self.zoom

    def jump_to(self, now: float, stored: tuple[float, float, float]) -> None:
        """Go straight to a stored PTZ position (the fake has no travel time)."""
        self.halt(now)
        self.pan, self.tilt, self.zoom = stored

    def integrate(self, now: float) -> None:
        """Move the position on by the velocity, up to when the velocity expires."""
        until = min(now, self.velocity_until)
        elapsed = until - self.integrated_at
        if elapsed > 0 and self.kind == CameraKind.CROP:
            self.crop_window.drive(self.velocity, elapsed)
        elif elapsed > 0:
            pan, tilt, zoom = self.velocity
            self.pan = clamp(self.pan + pan * PTZ_LIMITS.pan_speed_max * elapsed, PAN_RANGE)
            self.tilt = clamp(self.tilt + tilt * PTZ_LIMITS.tilt_speed_max * elapsed, TILT_RANGE)
            self.zoom = clamp(self.zoom + zoom * PTZ_LIMITS.zoom_speed_max * elapsed, ZOOM_RANGE)
        self.integrated_at = max(self.integrated_at, now)

    def position(self) -> Position | None:
        """Degrees and zoom for a PTZ, the window centre for a crop camera, else None."""
        if self.kind == CameraKind.CROP:
            return self.crop_window.position()
        if self.kind == CameraKind.PTZ:
            return Position(pan=round(self.pan, 3), tilt=round(self.tilt, 3), zoom=self.zoom)
        return None
