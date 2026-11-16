"""Tests for a crop camera's held velocity (cameras/crop_motion.py; was obs_crop_motion.py).

`CropMotion` is told the time on every call, so these tests just pass
numbers: no clock, no threads. It answers "move the window at this velocity
for this long" each time the driver's worker asks.
"""

import pytest

from baja_studio.cameras.crop_config import ObsCropSettings
from baja_studio.cameras.crop_motion import CropMotion, MotionStep, next_window
from baja_studio.cameras.crop_window import CropFrame, CropWindow, WindowTarget
from baja_studio.cameras.driver import AT_REST, Velocity

RIGHT = Velocity(pan=0.5)


def test_at_rest_there_is_nothing_to_move() -> None:
    motion = CropMotion()

    step = motion.advance(now=1.0)

    assert step.velocity == AT_REST
    assert step.seconds == 0.0
    assert not motion.moving


def test_a_held_velocity_moves_for_the_time_between_steps() -> None:
    motion = CropMotion()
    motion.move(RIGHT, ttl_seconds=0.3, now=10.0)

    # The worker steps 40 ms later, then another 40 ms later.
    first = motion.advance(now=10.04)
    second = motion.advance(now=10.08)

    assert first.velocity == RIGHT
    assert first.seconds == pytest.approx(0.04)
    assert second.seconds == pytest.approx(0.04)


def test_a_velocity_not_renewed_ends_at_its_ttl_and_not_a_moment_later() -> None:
    # The dead-man switch: a page that stops sending (a dropped Wi-Fi) must not
    # leave the window drifting.
    motion = CropMotion()
    motion.move(RIGHT, ttl_seconds=0.3, now=0.0)

    step = motion.advance(now=1.0)  # the worker looked only after a second

    assert step.seconds == pytest.approx(0.3)  # only the 0.3 s the velocity lived
    assert not motion.moving
    assert motion.advance(now=2.0).seconds == 0.0


def test_renewing_the_velocity_extends_it() -> None:
    motion = CropMotion()
    motion.move(RIGHT, ttl_seconds=0.3, now=0.0)
    motion.move(RIGHT, ttl_seconds=0.3, now=0.2)  # the page's next repeat

    assert motion.advance(now=0.45).seconds == pytest.approx(0.45)
    assert motion.moving


def test_stop_ends_the_motion_at_once() -> None:
    motion = CropMotion()
    motion.move(RIGHT, ttl_seconds=0.3, now=0.0)
    motion.advance(now=0.1)

    motion.stop()

    assert not motion.moving
    assert motion.advance(now=0.2).seconds == 0.0


def test_a_move_after_a_rest_does_not_count_the_time_spent_at_rest() -> None:
    motion = CropMotion()
    motion.advance(now=0.0)  # the worker looked while nothing moved

    motion.move(RIGHT, ttl_seconds=0.3, now=5.0)  # five seconds later, a move

    assert motion.advance(now=5.04).seconds == pytest.approx(0.04)


# --- next_window: one step of jumps and motion (shared by obs_crop and the camera node) ---


def test_a_jump_with_a_width_sets_the_windows_width_too() -> None:
    # The camera node's PUT /crop: centre (0.3, 0.4), width 0.6 of the frame.
    frame = CropFrame(3840, 2160, 16 / 9, 0.5)
    start = frame.whole()

    window, error = next_window(
        frame, start, WindowTarget(0.3, 0.4, width=0.6), MotionStep(AT_REST, 0.0), ObsCropSettings()
    )

    assert error == ""
    assert window == CropWindow(0.3, 0.4, 0.6)


def test_a_jump_narrower_than_min_window_is_widened_and_kept_inside() -> None:
    frame = CropFrame(3840, 2160, 16 / 9, 0.5)

    window, _ = next_window(
        frame,
        frame.whole(),
        WindowTarget(0.05, 0.05, width=0.1),
        MotionStep(AT_REST, 0.0),
        ObsCropSettings(),
    )

    # Half the width at the narrowest, and its centre moved in so it fits.
    assert window == CropWindow(0.25, 0.25, 0.5)


def test_without_a_jump_or_motion_nothing_moves() -> None:
    frame = CropFrame(3840, 2160, 16 / 9, 0.5)

    assert next_window(frame, frame.whole(), None, MotionStep(AT_REST, 0.0), ObsCropSettings()) == (
        None,
        "",
    )


def test_a_preset_that_is_not_stored_is_an_error_and_moves_nothing() -> None:
    frame = CropFrame(3840, 2160, 16 / 9, 0.5)

    window, error = next_window(
        frame, frame.whole(), 2, MotionStep(AT_REST, 0.0), ObsCropSettings()
    )

    assert window is None
    assert error == "preset 3 is not stored"
