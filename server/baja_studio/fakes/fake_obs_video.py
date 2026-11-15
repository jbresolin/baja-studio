"""The fake OBS's picture: filters applied at video frames, camera reopen delays, drawing.

What is modelled, and where it comes from (read, not measured; the WP6b bench
sheet checks them on OBS 33):

- **Crop/Pad filter (`crop_filter`)**, OBS's `plugins/obs-filters/crop-filter.c`:
  settings `relative` (default true), `left`, `top`, `right`, `bottom`, `cx`,
  `cy` (default 0). Relative: the output is the source minus the four edges;
  not relative: `left`/`top` are the corner and `cx`/`cy` the size. The
  output is clamped to **at least 1x1** (a crop larger than the picture
  leaves one pixel, not nothing). A disabled filter passes the picture through.
- **New edges apply at the next video frame**: the filter's update only
  stores the settings, and its size is worked out in its `video_tick`. So
  for up to one frame (`FRAME_SECONDS`, 30 fps) after a change the picture,
  and a screenshot of it, still has the old size. A filter just created is
  not in the picture until then either.
- **A capture device switched to another resolution reopens on its own
  thread**: `InputSettingsChanged` comes at once, the new size only
  `REOPEN_SECONDS` later (an assumed delay; the bench measures it).
"""

import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

CROP_FILTER_KIND = "crop_filter"
CROP_DEFAULTS: dict[str, Any] = {
    "relative": True,
    "left": 0,
    "top": 0,
    "right": 0,
    "bottom": 0,
    "cx": 0,
    "cy": 0,
}
FRAME_SECONDS = 1 / 30
REOPEN_SECONDS = 0.5


def next_frame_after(moment: float) -> float:
    """When the first video frame after `moment` is rendered (frames every 1/30 s)."""
    return (math.floor(moment / FRAME_SECONDS + 1e-9) + 1) * FRAME_SECONDS


@dataclass
class FakeFilter:
    """One filter on a source, with the settings its picture still uses until the next frame."""

    name: str
    kind: str
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    # The settings the picture used before the last change (None: not in the picture yet).
    previous: dict[str, Any] | None = None
    changed_at: float = -math.inf

    def full_settings(self) -> dict[str, Any]:
        """The settings with the kind's defaults filled in (what OBS reports)."""
        defaults = CROP_DEFAULTS if self.kind == CROP_FILTER_KIND else {}
        return {**defaults, **self.settings}

    def change(self, settings: dict[str, Any], now: float) -> None:
        """New settings; the picture follows at the next frame."""
        self.previous = self.picture_settings(now)
        self.settings = settings
        self.changed_at = now

    def picture_settings(self, now: float) -> dict[str, Any] | None:
        """The settings the picture uses at `now` (None: the filter is not in it yet)."""
        if math.isinf(self.changed_at) or now >= next_frame_after(self.changed_at) - 1e-9:
            return self.full_settings()
        return self.previous


@dataclass
class VideoInput:
    """A capture device's picture size, and a new size it is reopening with."""

    width: int
    height: int
    reopening: tuple[float, int, int] | None = None  # (ready at, width, height)

    def size(self, now: float) -> tuple[int, int]:
        """The size of the picture at `now`."""
        if self.reopening is not None and now >= self.reopening[0] - 1e-9:
            _, self.width, self.height = self.reopening
            self.reopening = None
        return self.width, self.height


@dataclass(frozen=True)
class Region:
    """A part of a source in its own pixels: where the picture after the filters comes from."""

    left: float
    top: float
    width: float
    height: float


def cropped(region: Region, settings: dict[str, Any]) -> Region:
    """The part of `region` a Crop/Pad filter with `settings` lets through (at least 1x1)."""
    left, top = float(settings.get("left", 0)), float(settings.get("top", 0))
    if settings.get("relative", True):
        width = region.width - left - float(settings.get("right", 0))
        height = region.height - top - float(settings.get("bottom", 0))
    else:
        width, height = float(settings.get("cx", 0)), float(settings.get("cy", 0))
    return Region(region.left + left, region.top + top, max(1.0, width), max(1.0, height))


def checkerboard_png(region: Region, width: int, height: int, square: float) -> bytes:
    """A grayscale PNG of `region` of a checkerboard with `square`-pixel squares, scaled."""
    columns = [
        int((region.left + (x + 0.5) * region.width / width) // square) % 2 for x in range(width)
    ]
    rows = {
        parity: b"\x00" + bytes(0xA0 if (column + parity) % 2 else 0x50 for column in columns)
        for parity in (0, 1)
    }
    raw = b"".join(
        rows[int((region.top + (y + 0.5) * region.height / height) // square) % 2]
        for y in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )
