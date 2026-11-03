"""Tests for velocity shaping (cameras/motion/shaping.py): deadband, inversion, scale, curve."""

import pytest

from baja_studio.cameras.config import MotionLimits, SpeedCurve
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.motion.shaping import apply_curve, apply_deadband, shape

NO_LIMITS = MotionLimits()


def test_inside_the_deadband_is_zero_whatever_the_sign() -> None:
    # A joystick at rest wobbles by a few percent; that must not move anything.
    assert apply_deadband(0.03, 0.04) == 0.0
    assert apply_deadband(-0.04, 0.04) == 0.0


def test_outside_the_deadband_motion_starts_from_zero_and_reaches_one() -> None:
    # Just past the deadband is barely moving; full deflection is still full speed.
    assert apply_deadband(0.05, 0.04) == pytest.approx(0.01 / 0.96)
    assert apply_deadband(1.0, 0.04) == 1.0
    assert apply_deadband(-1.0, 0.04) == -1.0


def test_the_squared_curve_keeps_the_sign_and_gives_fine_control_near_the_middle() -> None:
    assert apply_curve(0.5, SpeedCurve.SQUARED) == 0.25
    assert apply_curve(-0.5, SpeedCurve.SQUARED) == -0.25
    assert apply_curve(0.5, SpeedCurve.LINEAR) == 0.5


def test_inversion_flips_only_the_axes_it_names() -> None:
    limits = MotionLimits(invert_pan=True)

    shaped = shape(Velocity(0.5, 0.5, 0.5), limits, SpeedCurve.LINEAR, deadband=0.0)

    assert shaped == Velocity(-0.5, 0.5, 0.5)


def test_speed_scales_slow_each_axis_down() -> None:
    limits = MotionLimits(pan_speed_scale=0.5, tilt_speed_scale=0.25, zoom_speed_scale=0.0)

    shaped = shape(Velocity(1.0, 1.0, 1.0), limits, SpeedCurve.LINEAR, deadband=0.0)

    assert shaped == Velocity(0.5, 0.25, 0.0)


def test_zoom_is_not_deadbanded_because_the_zoom_buttons_send_a_fixed_speed() -> None:
    shaped = shape(Velocity(zoom=0.03), NO_LIMITS, SpeedCurve.LINEAR, deadband=0.04)

    assert shaped.zoom == 0.03


def test_values_beyond_one_are_clamped_first() -> None:
    shaped = shape(Velocity(pan=3.0, tilt=-2.0), NO_LIMITS, SpeedCurve.LINEAR, deadband=0.0)

    assert shaped == Velocity(1.0, -1.0, 0.0)
