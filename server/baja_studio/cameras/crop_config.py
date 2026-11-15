"""Settings of an `obs_crop` camera (WP6b, ADR 0020): its filter, limits and stored windows.

Part of a camera's entry in the registry (`config.CameraConfig.crop`); kept
in its own file so the registry's models stay readable.
"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from baja_studio.agents.contract import MAX_PRESET_NUMBER

if TYPE_CHECKING:
    from baja_studio.cameras.config import CameraConfig
    from baja_studio.cameras.crop_window import CropWindow


class StrictModel(BaseModel):
    """Refuses unknown keys, like the rest of the registry's settings."""

    model_config = ConfigDict(extra="forbid")


class CropPresetWindow(StrictModel):
    """A stored crop window (an `obs_crop` camera's preset): centre and width, fractions."""

    n: int = Field(ge=0, le=MAX_PRESET_NUMBER)
    centre_x: float = Field(ge=0.0, le=1.0)
    centre_y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0, description="Window width as a fraction of the source's.")


class ObsCropSettings(StrictModel):
    """How an `obs_crop` camera moves its OBS Crop/Pad filter (ADR 0020).

    Units are the agent contract's crop units (docs/agent-api.md §2.3): the
    window centre in fractions of the source from its top-left corner,
    speeds in frame fractions (zoom range) per second at full deflection.
    """

    filter_name: str = Field(
        default="Baja crop",
        min_length=1,
        max_length=100,
        description="The Crop/Pad filter on the camera's OBS source that this camera moves.",
    )
    min_window: float = Field(
        default=0.5,
        ge=0.05,
        le=1.0,
        description=(
            "Narrowest window, as a fraction of the source's width (0.5 on a 4K source "
            "gives 1080p without upscaling)."
        ),
    )
    aspect: str = Field(
        default="16:9",
        pattern=r"^[1-9][0-9]{0,2}:[1-9][0-9]{0,2}$",
        description="The window's shape: the output's aspect ratio.",
    )
    update_hz: int = Field(
        default=25, ge=5, le=60, description="How often a moving window is sent to OBS."
    )
    pan_speed: float = Field(
        default=0.3, gt=0.0, le=2.0, description="Frame widths per second at full deflection."
    )
    tilt_speed: float = Field(
        default=0.3, gt=0.0, le=2.0, description="Frame heights per second at full deflection."
    )
    speed_follows_zoom: bool = Field(
        default=True,
        description=(
            "Pan and tilt slow down as the window narrows, so the picture moves at the same "
            "speed on air at any zoom (unlike the agent contract's crop units)."
        ),
    )
    zoom_speed: float = Field(
        default=0.5, gt=0.0, le=2.0, description="Zoom range (wide to tele) per second."
    )
    preview_source: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "An OBS source showing this camera without the crop, for the preview (optional; "
            "without one the preview pieces the frame together from the windows it saw)."
        ),
    )
    windows: list[CropPresetWindow] = Field(
        default_factory=list, description="Stored presets (written by Store preset)."
    )

    @property
    def aspect_ratio(self) -> float:
        """The window's width over its height in pixels, e.g. 16/9."""
        width, height = self.aspect.split(":")
        return int(width) / int(height)

    def window(self, n: int) -> CropPresetWindow | None:
        """Stored preset `n`, if any."""
        return next((window for window in self.windows if window.n == n), None)


def cameras_with_crop_window(
    cameras: "list[CameraConfig]", camera_id: str, n: int, window: "CropWindow"
) -> list[dict[str, Any]]:
    """The camera list as settings JSON, with `window` stored as preset `n` of `camera_id`."""
    stored = CropPresetWindow(
        n=n,
        centre_x=round(window.centre_x, 6),
        centre_y=round(window.centre_y, 6),
        width=round(window.width, 6),
    )
    entries = []
    for camera in cameras:
        entry = camera.model_dump(mode="json")
        if camera.id == camera_id:
            windows = [item for item in camera.crop.windows if item.n != n] + [stored]
            entry["crop"]["windows"] = [
                item.model_dump() for item in sorted(windows, key=lambda item: item.n)
            ]
        entries.append(entry)
    return entries
