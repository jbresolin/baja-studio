"""Studio-side presets for an agent's crop camera: stored windows, as for `obs_crop` (ADR 0023).

The agent contract gives crop cameras no presets (`preset_max: null`): a
crop camera's "preset" is just a window, which the studio can store and
send back with `PUT /crop`. So that the joystick's Call buttons, the
keys and the D200's preset buttons work on a camera node's cameras as on an
`obs_crop` camera, the studio keeps those windows itself, in the camera's
settings, exactly where an `obs_crop` camera keeps them (`crop.windows`:
centre and width), with the same numbers (0 to 127) and the preset names in
`presets` as for any direct camera.

- Store: the window the agent last reported (`GET /crop`, kept by
  `AgentCropMirror`), saved before the command answers.
- Recall: `PUT /crop` of the stored centre and width, with the height of the
  agent's own window shape (an agent's windows keep their shape: the node's
  are 16:9), moved inside the frame. A studio motion command, like a preset
  recall on a PTZ.

This applies only to crop cameras whose agent lists no presets of its own;
an agent that one day stores crop presets keeps its own.
"""

from collections.abc import Callable
from typing import Any

from baja_studio.agents.contract import CropRect
from baja_studio.cameras.crop_config import CropPresetWindow
from baja_studio.cameras.crop_window import CropWindow

# The same numbers as an `obs_crop` camera's presets (drivers/obs_crop.PRESET_MAX).
STUDIO_CROP_PRESET_MAX = 127


class CropPresetError(Exception):
    """A preset that cannot be stored or recalled now; the message is for the operator."""


def keeps_studio_presets(camera_info: dict[str, Any] | None) -> bool:
    """True for an agent crop camera whose agent keeps no presets (`preset_max` null)."""
    if camera_info is None or camera_info.get("kind") != "crop":
        return False
    return (camera_info.get("limits") or {}).get("preset_max") is None


def recall_rect(stored: CropPresetWindow, current: CropRect) -> CropRect:
    """The crop to `PUT` for a stored window: its centre and width, the agent's shape, inside."""
    width = min(stored.width, 1.0)
    height = min(1.0, width * current.h / current.w) if current.w > 0 else width
    x = min(max(stored.centre_x - width / 2, 0.0), 1.0 - width)
    y = min(max(stored.centre_y - height / 2, 0.0), 1.0 - height)
    return CropRect(x=x, y=y, w=width, h=height)


def stored_window(current: CropRect) -> CropWindow:
    """The window to store for the crop an agent reported: its centre and width."""
    return CropWindow(current.x + current.w / 2, current.y + current.h / 2, current.w)


class AgentCropPresets:
    """One agent crop camera's stored windows, read from and saved into its settings."""

    def __init__(
        self,
        stored: Callable[[int], CropPresetWindow | None],
        names: Callable[[], list[dict[str, Any]]],
        save: Callable[[int, CropWindow], None],
    ) -> None:
        """`stored(n)` reads preset `n` from the camera's settings, `names()` its preset
        names, and `save(n, window)` stores a window (under the settings store's lock)."""
        self._stored = stored
        self.names = names
        self._save = save

    def recall(self, n: int, current: CropRect | None) -> CropRect:
        """The crop for preset `n`; `CropPresetError` if it is not stored or the window unknown."""
        stored = self._stored(n)
        if stored is None:
            raise CropPresetError(f"preset {n + 1} is not stored")
        if current is None:
            raise CropPresetError("the crop window has not been read from the agent yet")
        return recall_rect(stored, current)

    def store(self, n: int, current: CropRect | None) -> None:
        """Store the agent's current window as preset `n`, now."""
        if current is None:
            raise CropPresetError(
                f"cannot store preset {n + 1}: the crop window has not been read from the agent"
            )
        self._save(n, stored_window(current))
