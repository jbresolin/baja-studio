"""Simulated image controls for the fake agent's cameras, behaving like a UVC webcam.

Values are raw device numbers with a range and a step; a manual value is
ignored while the control's automatic mode is on, as real webcams do. This is
what lets the studio show "what actually took".
"""

from dataclasses import dataclass

from baja_studio.agents.contract import ImageControlChange, ImageControlValue


@dataclass
class SimulatedImageControl:
    """One image control with a range, a step and (maybe) an automatic mode."""

    value: float
    auto: bool | None
    min: float
    max: float
    step: float
    locked: bool = False

    def apply(self, change: ImageControlChange) -> None:
        """Like a webcam: a value is ignored while auto is on, and snapped to the range."""
        if change.auto is not None and self.auto is not None:
            self.auto = change.auto
        if change.locked is not None:
            self.locked = change.locked
        if change.value is not None and not self.auto:
            steps = round((change.value - self.min) / self.step)
            self.value = min(self.max, max(self.min, self.min + steps * self.step))

    def read_back(self) -> ImageControlValue:
        """The control as `GET /image` reports it."""
        return ImageControlValue(
            value=self.value,
            auto=self.auto,
            locked=self.locked,
            min=self.min,
            max=self.max,
            step=self.step,
        )


def webcam_image_controls() -> dict[str, SimulatedImageControl]:
    return {
        "exposure": SimulatedImageControl(value=-6, auto=True, min=-13, max=-1, step=1),
        "gain": SimulatedImageControl(value=0, auto=None, min=0, max=255, step=1),
        "white_balance": SimulatedImageControl(value=4600, auto=True, min=2800, max=6500, step=10),
        "focus": SimulatedImageControl(value=0, auto=True, min=0, max=255, step=5),
    }
