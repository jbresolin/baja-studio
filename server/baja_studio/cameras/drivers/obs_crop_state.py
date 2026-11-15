"""What an OBS crop camera publishes: the contract's state fields, and its `crop` window.

`state.cameras.cameras.<id>.crop` is what the preview draws (ADR 0020):

- `x`, `y`, `w`, `h`: the window as fractions of the source (top-left and
  size, the axes of the contract's `CropRect`);
- `centre_x`, `centre_y`, `zoom` (0 wide .. 1 at `min_window`) and
  `magnification` (1.0 at the widest window; the page shows "1.6x");
- `source_width`, `source_height`: the source's pixels, as measured.

Agent crop cameras publish the same keys they can know (`agent_crop.py`).
While the window moves, position and crop are published at most ten times
a second (the state goes to every page); at rest, at once.
"""

from collections.abc import Callable
from typing import Any

from baja_studio.agents.contract import Position, Status
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_window import CropFrame, CropWindow

MOVING_PUBLISH_INTERVAL_SECONDS = 0.1


def crop_state(frame: CropFrame, window: CropWindow, config: CameraConfig) -> dict[str, Any]:
    """The `crop` entry for a window on a frame."""
    rect = frame.rect(window)
    return {
        **{key: round(value, 6) for key, value in rect.items()},
        "centre_x": round(window.centre_x, 6),
        "centre_y": round(window.centre_y, 6),
        "zoom": round(frame.zoom_of(window), 4),
        "magnification": round(frame.magnification(window), 3),
        "source_width": frame.source_width,
        "source_height": frame.source_height,
        "filter": config.crop.filter_name,
    }


class CropPublisher:
    """Publishes a crop camera's fields when they change, position at most 10 Hz while moving."""

    def __init__(self, publish: Callable[[str, Any], None]) -> None:
        """`publish(key, value)` is the driver host's."""
        self._publish = publish
        self._published: dict[str, Any] = {}
        self._last_window_publish = -1e9
        self.position: Position | None = None

    def publish(
        self,
        now: float,
        *,
        moving: bool,
        crop: dict[str, Any] | None,
        status: Status,
        reason: str,
        last_error: str,
    ) -> None:
        """Write the fields that changed; status last, so "ok" arrives with its window."""
        self.position = (
            Position(pan=crop["centre_x"], tilt=crop["centre_y"], zoom=crop["zoom"])
            if crop
            else None
        )
        fields: dict[str, Any] = {}
        due = not moving or now - self._last_window_publish >= MOVING_PUBLISH_INTERVAL_SECONDS
        if due:
            fields["crop"] = crop
            fields["position"] = self.position.model_dump() if self.position else None
        fields.update(moving=moving, last_error=last_error, status_reason=reason)
        fields["status"] = status.value
        changed = False
        for key, value in fields.items():
            if self._published.get(key, ...) != value:
                self._published[key] = value
                self._publish(key, value)
                changed = changed or key in ("crop", "position")
        if due and changed:
            self._last_window_publish = now
