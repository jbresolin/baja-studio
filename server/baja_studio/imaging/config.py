"""Settings models of WP6a's image settings and USB cameras, used in `CameraConfig`.

Kept apart from `cameras/config.py` only for size; they are part of each
camera's entry in the cameras module's settings.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baja_studio.agents.contract import MAX_PRESET_NUMBER


class StrictModel(BaseModel):
    """Settings items refuse unknown keys, so a typo is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid")


class UsbDeviceRef(StrictModel):
    """Which USB camera on the studio PC this is (WP6a), as picked from the device list.

    The DirectShow device path (stable per USB port) identifies it; several
    S600s share one name. Only a device without a path is found by `name`
    and `index` (its place among the devices with that name).
    """

    path: str = Field(default="", max_length=1000, description="DirectShow device path.")
    name: str = Field(default="", max_length=200, description="Device name, for display.")
    index: int = Field(
        default=0, ge=0, le=31, description="Which of the devices with this name (0: the first)."
    )

    @model_validator(mode="after")
    def _path_or_name(self) -> Self:
        if not self.path and not self.name:
            raise ValueError("a USB device needs its path or, failing that, its name")
        return self


class ImageSetting(StrictModel):
    """One saved image control (WP6a): a raw device value and whether it is on automatic.

    `value` None keeps whatever the device has; `auto` None means the control
    has no automatic mode.
    """

    value: float | None = None
    auto: bool | None = None


class StoredPosition(StrictModel):
    """A UVC camera's preset (WP6a): absolute pan, tilt and zoom in device units.

    The Pixy's own preset slots do not keep zoom, so the studio keeps them.
    """

    n: int = Field(ge=0, le=MAX_PRESET_NUMBER)
    pan: float
    tilt: float
    zoom: float
