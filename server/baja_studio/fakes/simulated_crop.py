"""The window of a simulated crop camera: a digital pan, tilt and zoom on a fixed frame.

A `/move` drives the window as a PTZ's velocities drive its head
(docs/agent-api.md, "Motion"): pan moves the centre right, tilt moves it up,
zoom shrinks the window around its centre. The window keeps the shape it
was given by `PUT /crop` and always stays inside the frame.
"""

from dataclasses import dataclass

from baja_studio.agents.contract import CameraLimits, CropRect, Position

# The narrowest window, as its longer side: zoom 1. The whole frame is zoom 0.
MIN_WINDOW_SIZE = 0.1
# Window centre in frame fractions per second, zoom range per second.
CROP_LIMITS = CameraLimits(
    pan_speed_max=0.5, tilt_speed_max=0.5, zoom_speed_max=0.5, preset_max=None
)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass
class CropWindow:
    """Centre (fractions of the frame), size (the longer side) and shape (w, h at size 1)."""

    centre_x: float = 0.5
    centre_y: float = 0.5
    size: float = 1.0
    shape: tuple[float, float] = (1.0, 1.0)

    @classmethod
    def from_rect(cls, rect: CropRect) -> "CropWindow":
        """The window for a requested crop; one narrower than the minimum is widened."""
        longer = max(rect.w, rect.h)
        window = cls(
            centre_x=rect.x + rect.w / 2,
            centre_y=rect.y + rect.h / 2,
            size=max(longer, MIN_WINDOW_SIZE),
            shape=(rect.w / longer, rect.h / longer),
        )
        window._keep_inside()
        return window

    def rect(self) -> CropRect:
        """The window as a crop of the frame."""
        w, h = self._extent()
        return CropRect(
            x=_clamp(self.centre_x - w / 2, 0.0, 1.0 - w),
            y=_clamp(self.centre_y - h / 2, 0.0, 1.0 - h),
            w=w,
            h=h,
        )

    def zoom(self) -> float:
        """0 for the whole frame, 1 for the narrowest window."""
        return _clamp((1.0 - self.size) / (1.0 - MIN_WINDOW_SIZE), 0.0, 1.0)

    def position(self) -> Position:
        """The window centre and zoom, as `Position` reports them for crop cameras."""
        return Position(pan=round(self.centre_x, 6), tilt=round(self.centre_y, 6), zoom=self.zoom())

    def drive(self, velocity: tuple[float, float, float], seconds: float) -> None:
        """Move the window at `velocity` (pan, tilt, zoom; -1..1 each) for `seconds`."""
        pan, tilt, zoom = velocity
        new_zoom = _clamp(self.zoom() + zoom * CROP_LIMITS.zoom_speed_max * seconds, 0.0, 1.0)
        self.size = 1.0 - new_zoom * (1.0 - MIN_WINDOW_SIZE)
        self.centre_x += pan * CROP_LIMITS.pan_speed_max * seconds
        # Frame y grows downwards; a positive tilt looks up, as on a PTZ.
        self.centre_y -= tilt * CROP_LIMITS.tilt_speed_max * seconds
        self._keep_inside()

    def _extent(self) -> tuple[float, float]:
        return self.shape[0] * self.size, self.shape[1] * self.size

    def _keep_inside(self) -> None:
        w, h = self._extent()
        self.centre_x = _clamp(self.centre_x, w / 2, 1.0 - w / 2)
        self.centre_y = _clamp(self.centre_y, h / 2, 1.0 - h / 2)
