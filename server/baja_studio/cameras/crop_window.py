"""The geometry of a digital PTZ: a window on a fixed source frame (ADR 0020).

Units are the agent contract's crop units (docs/agent-api.md §2.3), so a crop
camera means the same thing wherever it lives:

- the window's **centre** is in fractions `0..1` of the source frame from its
  top-left corner; a positive tilt looks up, so it makes the centre's y smaller;
- the window's **width** is a fraction of the source's width; its height
  follows from the output's aspect ratio (16:9), so the window always has the
  output's shape whatever the source's shape;
- **zoom** is `0` at the widest window that fits and `1` at the narrowest
  allowed (`min_window`), linear in the width in between; **magnification**
  is how many times narrower than the widest window it is (the page shows "1.6x").

Everything here is pure arithmetic: the OBS driver (`drivers/obs_crop.py`)
turns windows into the pixel edges of a Crop/Pad filter with `pixels`.
"""

from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from baja_studio.cameras.driver import Velocity


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass(frozen=True)
class CropWindow:
    """Where the window is: its centre (fractions of the frame) and its width (fraction)."""

    centre_x: float
    centre_y: float
    width: float


@dataclass(frozen=True)
class PixelCrop:
    """A Crop/Pad filter's four edges in source pixels (its "relative" mode)."""

    left: int
    top: int
    right: int
    bottom: int

    def filter_settings(self) -> dict[str, Any]:
        """The settings sent to OBS (`relative` on, so the edges are cut from the source)."""
        return {
            "relative": True,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }

    @classmethod
    def from_filter_settings(cls, settings: dict[str, Any]) -> "PixelCrop":
        """The edges in a Crop/Pad filter's settings (missing ones are 0, as in OBS)."""
        return cls(
            left=int(settings.get("left", 0)),
            top=int(settings.get("top", 0)),
            right=int(settings.get("right", 0)),
            bottom=int(settings.get("bottom", 0)),
        )


@dataclass(frozen=True)
class CropSpeeds:
    """Top speeds at velocity 1: frame widths/s, frame heights/s, zoom range/s."""

    pan: float
    tilt: float
    zoom: float


@dataclass(frozen=True)
class CropFrame:
    """A source of `source_width` x `source_height` pixels, cut to windows of one shape."""

    source_width: int
    source_height: int
    aspect: float
    min_window: float

    # --- sizes ---------------------------------------------------------------------------

    def height_of(self, width: float) -> float:
        """A window's height (fraction of the source's height) for its width (fraction)."""
        return width * self.source_width / (self.aspect * self.source_height)

    @property
    def widest(self) -> float:
        """The widest window that fits: the whole width, or less on a taller source."""
        return min(1.0, self.aspect * self.source_height / self.source_width)

    @property
    def narrowest(self) -> float:
        """The narrowest window allowed (`min_window`, but never wider than the widest)."""
        return min(self.min_window, self.widest)

    def zoom_of(self, window: CropWindow) -> float:
        """0 at the widest window, 1 at the narrowest."""
        span = self.widest - self.narrowest
        if span <= 0:
            return 0.0
        return _clamp((self.widest - window.width) / span, 0.0, 1.0)

    def width_for_zoom(self, zoom: float) -> float:
        """The window width for a zoom of 0..1."""
        return self.widest - _clamp(zoom, 0.0, 1.0) * (self.widest - self.narrowest)

    def magnification(self, window: CropWindow) -> float:
        """How many times narrower than the widest window: 1.0 wide, 2.0 at half the width."""
        return self.widest / window.width

    # --- keeping windows inside ------------------------------------------------------------

    def clamp(self, window: CropWindow) -> CropWindow:
        """The nearest allowed window: no narrower than `min_window`, inside the frame."""
        width = _clamp(window.width, self.narrowest, self.widest)
        height = self.height_of(width)
        return CropWindow(
            centre_x=_clamp(window.centre_x, width / 2, 1.0 - width / 2),
            centre_y=_clamp(window.centre_y, height / 2, 1.0 - height / 2),
            width=width,
        )

    def whole(self) -> CropWindow:
        """The widest window, centred: where a camera starts when OBS shows no crop yet."""
        return CropWindow(0.5, 0.5, self.widest)

    def drive(
        self,
        window: CropWindow,
        velocity: Velocity,
        seconds: float,
        speeds: CropSpeeds,
        follow_zoom: bool = False,
    ) -> CropWindow:
        """The window after moving at `velocity` (-1..1 each) for `seconds`.

        Zoom grows or shrinks the window around its centre; pan and tilt move
        the centre. The result is clamped, so holding a direction at an edge
        just stays there. With `follow_zoom`, pan and tilt are scaled by the
        window's width (half as fast in frame fractions at 2x), so the picture
        on air moves at the same speed whatever the zoom.
        """
        scale = window.width / self.widest if follow_zoom else 1.0
        zoom = self.zoom_of(window) + velocity.zoom * speeds.zoom * seconds
        moved = CropWindow(
            centre_x=window.centre_x + velocity.pan * speeds.pan * scale * seconds,
            # Frame y grows downwards; a positive tilt looks up, as on a PTZ.
            centre_y=window.centre_y - velocity.tilt * speeds.tilt * scale * seconds,
            width=self.width_for_zoom(zoom) if velocity.zoom else window.width,
        )
        return self.clamp(moved)

    def centred_on(self, window: CropWindow, centre_x: float, centre_y: float) -> CropWindow:
        """The same window moved so its centre is at (centre_x, centre_y), as far as it fits."""
        return self.clamp(replace(window, centre_x=centre_x, centre_y=centre_y))

    # --- fractions and pixels -------------------------------------------------------------

    def rect(self, window: CropWindow) -> dict[str, float]:
        """The window as a crop rectangle (`CropRect`'s x, y, w, h: top-left and size)."""
        height = self.height_of(window.width)
        return {
            "x": window.centre_x - window.width / 2,
            "y": window.centre_y - height / 2,
            "w": window.width,
            "h": height,
        }

    def pixels(self, window: CropWindow) -> PixelCrop:
        """The filter edges that show `window`, rounded to whole pixels inside the source."""
        width_px = max(1, min(self.source_width, round(window.width * self.source_width)))
        height_px = max(1, min(self.source_height, round(width_px / self.aspect)))
        left = round(window.centre_x * self.source_width - width_px / 2)
        top = round(window.centre_y * self.source_height - height_px / 2)
        left = int(_clamp(left, 0, self.source_width - width_px))
        top = int(_clamp(top, 0, self.source_height - height_px))
        return PixelCrop(
            left=left,
            top=top,
            right=self.source_width - left - width_px,
            bottom=self.source_height - top - height_px,
        )

    def window_of(self, crop: PixelCrop) -> CropWindow:
        """The allowed window nearest to a filter's edges (adopting a crop made in OBS)."""
        width_px = max(1, self.source_width - crop.left - crop.right)
        height_px = max(1, self.source_height - crop.top - crop.bottom)
        return self.clamp(
            CropWindow(
                centre_x=(crop.left + width_px / 2) / self.source_width,
                centre_y=(crop.top + height_px / 2) / self.source_height,
                width=width_px / self.source_width,
            )
        )


@dataclass(frozen=True)
class WindowTarget:
    """Where to jump a crop camera's window: centred on a point, or the whole picture.

    Click-to-centre on a preview sends `centre_window`; `reset_crop` shows
    the whole picture (also a way out after the source's resolution shrank
    under a crop that no longer fits).
    """

    centre_x: float = 0.5
    centre_y: float = 0.5
    whole: bool = False

    def describe(self) -> str:
        """For the command tail: "centre_window 0.25,0.40" or "reset_crop"."""
        if self.whole:
            return "reset_crop"
        return f"centre_window {self.centre_x:.2f},{self.centre_y:.2f}"


@runtime_checkable
class WindowPlacingDriver(Protocol):
    """A driver whose crop window can jump (the OBS crop driver, agent crop cameras)."""

    def place_window(self, target: WindowTarget) -> None:
        """Jump the window to `target`; any velocity stops. Returns at once."""
