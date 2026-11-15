"""Image controls as the studio sees them, whichever camera they belong to, and the rules on them.

A control is a raw device number with a range and a step, an automatic mode
or none (`auto` None), and the device's default when it has one (USB). USB
controls come from the UVC child, agent controls from `GET /image` (contract
decision 10). The rules here are plain functions: what a change may be, what
Lock, All auto, Reset and Copy turn into, and what is saved after a write.
"""

from dataclasses import dataclass
from typing import Any

from baja_studio.agents.contract import ImageControlValue
from baja_studio.imaging.config import ImageSetting

# The order cards show controls in; anything else follows, alphabetically.
DISPLAY_ORDER = (
    "exposure",
    "gain",
    "white_balance",
    "focus",
    "iris",
    "brightness",
    "contrast",
    "saturation",
    "sharpness",
    "gamma",
    "hue",
    "backlight_compensation",
)
# UVC camera controls that move the camera rather than change the picture.
MOTION_CONTROLS = ("pan", "tilt", "zoom")
IMAGE_CONTROL_NAMES = list(DISPLAY_ORDER)


@dataclass
class ControlState:
    """One image control as last read from the device, and the error of its last write."""

    value: float
    auto: bool | None
    min: float
    max: float
    step: float
    default: float | None = None
    default_auto: bool | None = None
    error: str = ""

    @classmethod
    def from_usb(cls, data: dict[str, Any]) -> "ControlState":
        """From the UVC child's `read_controls` answer."""
        return cls(
            value=float(data["value"]),
            auto=data.get("auto"),
            min=float(data["min"]),
            max=float(data["max"]),
            step=float(data.get("step") or 1),
            default=None if data.get("default") is None else float(data["default"]),
            default_auto=data.get("default_auto"),
        )

    @classmethod
    def from_agent(cls, value: ImageControlValue) -> "ControlState":
        """From an agent's `GET /image` (agents give no defaults)."""
        return cls(value.value, value.auto, value.min, value.max, value.step or 1.0)

    def as_json(self) -> dict[str, Any]:
        """For the state: what a card draws its slider from."""
        return {
            "value": self.value,
            "auto": self.auto,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "default": self.default,
            "error": self.error,
        }


def ordered(names: list[str]) -> list[str]:
    """`names` in display order."""
    known = [name for name in DISPLAY_ORDER if name in names]
    return known + sorted(name for name in names if name not in DISPLAY_ORDER)


def snap(value: float, control: ControlState) -> float:
    """`value` clamped to the control's range and put on its step."""
    clamped = min(control.max, max(control.min, value))
    steps = round((clamped - control.min) / control.step)
    return min(control.max, control.min + steps * control.step)


def change_problem(control: ControlState, change: ImageSetting) -> str:
    """Why `change` cannot be sent to `control` ("" when it can).

    A value outside the range is refused rather than clamped: an operator
    typing -20 into a -13..-1 control should see that, not a silent -13.
    """
    if change.auto is not None and control.auto is None:
        return "this control has no automatic mode"
    if change.value is not None and not control.min <= change.value <= control.max:
        return f"{change.value:g} is outside {control.min:g}..{control.max:g}"
    return ""


def lock_changes(controls: dict[str, ControlState]) -> dict[str, ImageSetting]:
    """Lock: every control now on auto becomes manual at the value the camera chose."""
    return {
        name: ImageSetting(value=control.value, auto=False)
        for name, control in controls.items()
        if control.auto is True
    }


def all_auto_changes(controls: dict[str, ControlState]) -> dict[str, ImageSetting]:
    """All auto: every control that has an automatic mode goes back to it."""
    return {
        name: ImageSetting(auto=True)
        for name, control in controls.items()
        if control.auto is not None
    }


def default_changes(controls: dict[str, ControlState]) -> dict[str, ImageSetting]:
    """Reset: every control to the device's own default value and mode (USB devices know them)."""
    changes = {}
    for name, control in controls.items():
        if control.default is None:
            continue
        auto = control.default_auto if control.auto is not None else None
        changes[name] = ImageSetting(value=control.default, auto=auto)
    return changes


def copy_changes(
    source: dict[str, ControlState], target: dict[str, ControlState]
) -> tuple[dict[str, ImageSetting], list[str]]:
    """Copy settings from: what to write on the target, and the source controls it lacks.

    Only controls both cameras have are copied. A control on auto stays on
    auto where the target has an automatic mode; otherwise its value is
    clamped to the target's range and put on the target's step.
    """
    changes: dict[str, ImageSetting] = {}
    skipped = [name for name in source if name not in target]
    for name, theirs in source.items():
        ours = target.get(name)
        if ours is None:
            continue
        if theirs.auto is True and ours.auto is not None:
            changes[name] = ImageSetting(auto=True)
            continue
        auto = False if ours.auto is not None else None
        changes[name] = ImageSetting(value=snap(theirs.value, ours), auto=auto)
    return changes, skipped


def saved_after_write(control: ControlState) -> ImageSetting:
    """What to save for a control the device just took: auto, or the value it reads back."""
    if control.auto is True:
        return ImageSetting(auto=True)
    return ImageSetting(value=control.value, auto=control.auto)
