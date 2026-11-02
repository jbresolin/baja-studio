"""Tests for the fake agent's simulated image controls (fakes/simulated_image.py).

They behave like a UVC webcam's: values snap to the step, stay in range, and
are ignored while the automatic mode is on.
"""

from baja_studio.agents.contract import ImageControlChange
from baja_studio.fakes.simulated_image import SimulatedImageControl


def focus() -> SimulatedImageControl:
    """Focus from 0 to 255 in steps of 5, on auto, like a typical webcam."""
    return SimulatedImageControl(value=0, auto=True, min=0, max=255, step=5)


def test_a_value_is_ignored_while_auto_is_on() -> None:
    control = focus()

    control.apply(ImageControlChange(value=100))

    assert control.read_back().value == 0


def test_turning_auto_off_and_setting_a_value_in_one_change_works() -> None:
    control = focus()

    control.apply(ImageControlChange(auto=False, value=102))

    # 102 is not a multiple of 5 from 0; the nearest step is 100.
    assert (control.read_back().auto, control.read_back().value) == (False, 100)


def test_values_beyond_the_range_are_clamped() -> None:
    control = focus()
    control.apply(ImageControlChange(auto=False))

    control.apply(ImageControlChange(value=1000))

    assert control.read_back().value == 255


def test_a_control_without_auto_ignores_requests_to_turn_it_on() -> None:
    gain = SimulatedImageControl(value=0, auto=None, min=0, max=255, step=1)

    gain.apply(ImageControlChange(auto=True, value=10))

    assert (gain.read_back().auto, gain.read_back().value) == (None, 10)
