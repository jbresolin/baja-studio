"""Velocity shaping: deadband, inversion, speed scale and curve, applied in that order.

Pure functions; the arbiter calls `shape` on every velocity before it reaches
a driver, so every driver (and every future one) is shaped the same way.
"""

from baja_studio.cameras.config import MotionLimits, SpeedCurve
from baja_studio.cameras.driver import Velocity


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, value))


def apply_deadband(value: float, deadband: float) -> float:
    """0 inside the deadband; outside it, rescaled so motion starts smoothly from 0.

    Without the rescale a stick leaving the deadband would jump straight to
    `deadband` speed; with it, just outside the deadband is just above 0.
    """
    magnitude = abs(value)
    if magnitude <= deadband:
        return 0.0
    rescaled = (magnitude - deadband) / (1.0 - deadband)
    return rescaled if value > 0 else -rescaled


def apply_curve(value: float, curve: SpeedCurve) -> float:
    """Linear leaves the value alone; squared keeps the sign and squares the magnitude."""
    if curve == SpeedCurve.SQUARED:
        return value * abs(value)
    return value


def shape(velocity: Velocity, limits: MotionLimits, curve: SpeedCurve, deadband: float) -> Velocity:
    """The velocity the driver should get for what an input asked for.

    `deadband` is 0 for sources that are not physical sticks (web, API).
    """
    pan = apply_deadband(_clamp_unit(velocity.pan), deadband)
    tilt = apply_deadband(_clamp_unit(velocity.tilt), deadband)
    # Zoom is not deadbanded: the controller's zoom buttons give a fixed speed, not a stick.
    zoom = _clamp_unit(velocity.zoom)
    if limits.invert_pan:
        pan = -pan
    if limits.invert_tilt:
        tilt = -tilt
    return Velocity(
        pan=apply_curve(pan, curve) * limits.pan_speed_scale,
        tilt=apply_curve(tilt, curve) * limits.tilt_speed_scale,
        zoom=apply_curve(zoom, curve) * limits.zoom_speed_scale,
    )
