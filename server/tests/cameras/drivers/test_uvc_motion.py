"""Tests for the Pixy driver's arithmetic (cameras/drivers/uvc_motion.py), on numbers alone.

`PtzIntegrator` turns velocities into absolute positions: no clock of its
own, so each test says what time it is. Top speeds here are 60 units/s for
pan, 40 for tilt and 20 for zoom at velocity 1.0.
"""

from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.drivers.uvc_motion import AxisRange, PtzIntegrator, TopSpeeds

PIXY_RANGES = {
    "pan": AxisRange(-150, 150, 1),
    "tilt": AxisRange(-90, 90, 1),
    "zoom": AxisRange(100, 150, 1),
}


def connected(pan: float = 0, tilt: float = 0, zoom: float = 100) -> PtzIntegrator:
    """An integrator connected to a Pixy that reads back (pan, tilt, zoom)."""
    integrator = PtzIntegrator(TopSpeeds(pan=60, tilt=40, zoom=20))
    integrator.connect(PIXY_RANGES, {"pan": pan, "tilt": tilt, "zoom": zoom})
    return integrator


def test_nothing_is_written_until_something_moves() -> None:
    # Connecting reads the camera back; writing that again would be pointless.
    integrator = connected(pan=10)

    assert integrator.due_writes(now=5.0) == {}


def test_a_velocity_is_integrated_into_positions_over_time() -> None:
    integrator = connected()

    integrator.move(Velocity(pan=0.5, tilt=-1.0), ttl_seconds=1.0, now=0.0)

    # After 0.1 s: pan 0.5 x 60/s x 0.1 s = 3; tilt -1 x 40/s x 0.1 s = -4.
    assert integrator.due_writes(now=0.1) == {"pan": 3, "tilt": -4}


def test_only_axes_that_changed_by_a_whole_step_are_written() -> None:
    integrator = connected()
    integrator.move(Velocity(pan=1.0, tilt=0.001), ttl_seconds=1.0, now=0.0)

    due = integrator.due_writes(now=0.1)
    integrator.wrote(due)

    # Tilt moved 0.004 of a step: not worth a packet. Pan moved 6.
    assert due == {"pan": 6}
    assert integrator.due_writes(now=0.1) == {}


def test_positions_stop_at_the_ends_of_the_range() -> None:
    integrator = connected(pan=145)
    integrator.move(Velocity(pan=1.0), ttl_seconds=10.0, now=0.0)

    assert integrator.due_writes(now=2.0) == {"pan": 150}


def test_the_ttl_ends_the_motion_like_a_dead_man_switch() -> None:
    integrator = connected()
    integrator.move(Velocity(pan=1.0), ttl_seconds=0.3, now=0.0)

    # Integrated only up to 0.3 s (18 units), however late we look.
    assert integrator.due_writes(now=5.0) == {"pan": 18}
    assert not integrator.moving(now=5.0)


def test_stop_freezes_the_target_and_sends_nothing_more() -> None:
    integrator = connected()
    integrator.move(Velocity(pan=1.0), ttl_seconds=1.0, now=0.0)
    integrator.wrote(integrator.due_writes(now=0.1))

    integrator.stop(now=0.2)

    # What happened up to the stop still goes out (pan 12); nothing after it.
    assert integrator.due_writes(now=0.2) == {"pan": 12}
    integrator.wrote({"pan": 12})
    assert integrator.due_writes(now=3.0) == {}


def test_zoom_is_published_normalised_and_pan_tilt_in_device_units() -> None:
    integrator = connected(pan=-30, tilt=12, zoom=125)

    position = integrator.position()

    assert position is not None
    assert (position.pan, position.tilt, position.zoom) == (-30, 12, 0.5)


def test_a_jump_goes_straight_to_a_preset_and_ends_any_velocity() -> None:
    integrator = connected()
    integrator.move(Velocity(pan=1.0), ttl_seconds=1.0, now=0.0)

    integrator.jump({"pan": 40, "tilt": -20, "zoom": 140}, now=0.1)

    assert integrator.due_writes(now=0.5) == {"pan": 40, "tilt": -20, "zoom": 140}
    assert not integrator.moving(now=0.5)


def test_a_disconnected_integrator_has_no_position_and_writes_nothing() -> None:
    integrator = connected()

    integrator.disconnect()

    assert integrator.position() is None
    assert integrator.commanded() is None
    assert integrator.due_writes(now=1.0) == {}


def test_a_ttl_made_of_small_steps_still_ends_on_time() -> None:
    # Six 0.05 s steps add up to 0.30000000000000004 or 0.29999999999999993;
    # either way the motion ends at 0.3 s.
    integrator = connected()
    integrator.move(Velocity(pan=1.0), ttl_seconds=0.3, now=0.0)
    now = 0.0
    for _ in range(6):
        now += 0.05

    assert not integrator.moving(now)
