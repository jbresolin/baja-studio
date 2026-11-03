"""Tests for the motion arbiter (cameras/motion/arbiter.py).

The arbiter sits between every input (joystick, web page, HTTP API) and one
camera's driver. Here the driver is a `RecordingDriver`, which only writes
down what it was told, and time is the fake clock, moved with `advance`.
`tick()` is what the module's motion worker calls; tests call it directly.
"""

import logging
from typing import Any

import pytest

from baja_studio.agents.contract import Mode
from baja_studio.cameras.config import (
    CameraAddress,
    CameraConfig,
    MotionLimits,
    MotionSettings,
    SpeedCurve,
)
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.motion.arbiter import MotionArbiter, MotionRefusedError
from tests.cameras.recording_driver import RecordingDriver, RecordingModeDriver
from tests.conftest import FakeClock

JOYSTICK, WEB, API = "joystick:ctl1", "web:browser-1", "api:stream-deck"


class Setup:
    """An arbiter over a recording driver, with settings a test can change."""

    def __init__(self, fake_clock: FakeClock, driver: RecordingDriver | None = None) -> None:
        self.clock = fake_clock
        self.driver = driver or RecordingDriver()
        self.camera = CameraConfig(id="cam1", address=CameraAddress(host="10.0.0.9"))
        self.motion = MotionSettings()
        self.published: dict[str, Any] = {}
        self.arbiter = MotionArbiter(
            self.driver,
            config=lambda: self.camera,
            motion=lambda: self.motion,
            clock=fake_clock,
            publish=self.published.__setitem__,
            logger=logging.getLogger("test"),
        )

    def later(self, seconds: float) -> float:
        """Let time pass, then run one tick; returns how long the arbiter would sleep."""
        self.clock.advance(seconds)
        return self.arbiter.tick()


@pytest.fixture
def setup(fake_clock: FakeClock) -> Setup:
    return Setup(fake_clock)


# --- moving and the dead-man ------------------------------------------------------------


def test_a_move_reaches_the_driver_with_the_ttl(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.5), ttl_ms=300)

    assert setup.driver.calls == [("move", Velocity(pan=0.5), 300)]


def test_the_motion_settings_ttl_is_used_when_the_source_gives_none(setup: Setup) -> None:
    setup.motion = MotionSettings(ttl_ms=500)

    setup.arbiter.move(WEB, Velocity(tilt=0.5))

    assert setup.driver.calls == [("move", Velocity(tilt=0.5), 500)]


def test_a_move_that_is_not_renewed_stops_the_camera_when_its_ttl_runs_out(setup: Setup) -> None:
    # An API caller sends one move with a 300 ms ttl and never sends again.
    setup.arbiter.move(API, Velocity(pan=1.0), ttl_ms=300)
    setup.driver.clear()

    setup.later(0.15)
    assert "stop" not in setup.driver.names()

    setup.later(0.15)
    assert setup.driver.names()[-1] == "stop"
    assert setup.published["control"]["last_command"] == "expired"


def test_a_held_velocity_is_resent_every_resend_interval_with_the_time_it_has_left(
    setup: Setup,
) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.4), ttl_ms=300)
    setup.driver.clear()

    setup.later(0.1)

    # One resend, carrying the 200 ms the velocity still has.
    assert setup.driver.calls == [("move", Velocity(pan=0.4), 200)]


def test_the_worker_is_told_to_come_back_at_the_next_resend(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.4), ttl_ms=300)

    assert setup.arbiter.tick() == pytest.approx(0.1)


def test_with_nothing_held_the_worker_may_sleep_a_whole_second(setup: Setup) -> None:
    assert setup.arbiter.tick() == 1.0


def test_renewing_the_same_velocity_extends_it_without_sending_anything_new(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.4), ttl_ms=300)
    setup.driver.clear()

    setup.clock.advance(0.05)
    setup.arbiter.move(WEB, Velocity(pan=0.4), ttl_ms=300)

    assert setup.driver.calls == []  # same velocity: the next resend will carry it


def test_a_new_velocity_from_the_holder_is_sent_at_once(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.4), ttl_ms=300)

    setup.arbiter.move(WEB, Velocity(pan=0.8), ttl_ms=300)

    assert setup.driver.calls[-1] == ("move", Velocity(pan=0.8), 300)


def test_a_route_change_resends_the_held_velocity_straight_away(setup: Setup) -> None:
    # An agent camera switching to its direct fallback mid-hold: the new route
    # must hear the velocity now, not at the next resend.
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), ttl_ms=300)
    setup.arbiter.tick()
    setup.driver.clear()
    setup.driver.current_route = "fallback"

    setup.later(0.01)

    assert setup.driver.names() == ["move"]


# --- who holds the camera ------------------------------------------------------------------


def test_another_source_cannot_take_over_while_the_holder_is_moving(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0))

    with pytest.raises(MotionRefusedError, match="joystick:ctl1"):
        setup.arbiter.move(WEB, Velocity(tilt=1.0))


def test_another_source_takes_over_once_the_holder_is_at_rest(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0))
    setup.arbiter.move(JOYSTICK, Velocity())  # the stick is let go

    setup.arbiter.move(WEB, Velocity(tilt=1.0))

    assert setup.published["control"]["velocity_owner"] == WEB


def test_another_source_takes_over_once_the_holders_ttl_has_expired(setup: Setup) -> None:
    setup.arbiter.move(API, Velocity(pan=1.0), ttl_ms=300)
    setup.clock.advance(0.3)

    setup.arbiter.move(WEB, Velocity(tilt=1.0))

    assert setup.published["control"]["velocity_owner"] == WEB


def test_a_stop_from_anyone_is_always_executed(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0))

    setup.arbiter.stop(WEB)

    assert setup.driver.names()[-1] == "stop"
    assert setup.published["control"]["velocity_owner"] is None


def test_an_explicit_stop_is_sent_even_when_nothing_is_moving(setup: Setup) -> None:
    # The operator presses Stop because the camera looks wrong: always send it.
    assert setup.arbiter.stop(WEB)
    assert setup.driver.names() == ["stop"]


def test_a_joystick_stop_frame_with_nothing_held_sends_nothing(setup: Setup) -> None:
    # A controller at rest may repeat "stop"; on an agent camera each stop
    # would pause auto tracking, so these are not passed on.
    assert not setup.arbiter.stop(JOYSTICK, only_if_owner=True)
    assert setup.driver.calls == []


def test_a_joystick_stop_frame_stops_the_camera_the_joystick_owns(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), latched=True)

    assert setup.arbiter.stop(JOYSTICK, only_if_owner=True)
    assert setup.driver.names()[-1] == "stop"


def test_an_idle_controllers_repeated_stops_do_not_touch_a_camera_the_web_owns(
    setup: Setup,
) -> None:
    # The operator drives from the web page; the desk controller sits at rest,
    # repeating "stop". The web's move must carry on.
    setup.arbiter.move(WEB, Velocity(pan=1.0), ttl_ms=300)
    setup.driver.clear()

    stopped = [setup.arbiter.stop(JOYSTICK, only_if_owner=True) for _ in range(5)]

    assert stopped == [False] * 5
    assert setup.driver.calls == []
    assert setup.published["control"]["velocity_owner"] == WEB


def test_releasing_the_stick_stops_the_camera_once(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0))
    setup.driver.clear()

    setup.arbiter.move(JOYSTICK, Velocity())
    setup.arbiter.move(JOYSTICK, Velocity())  # still at rest: nothing more

    assert setup.driver.names() == ["stop"]


def test_another_source_at_rest_does_not_stop_the_holder(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=1.0))
    setup.driver.clear()

    with pytest.raises(MotionRefusedError):
        setup.arbiter.move(JOYSTICK, Velocity(pan=0.5))
    setup.arbiter.move(WEB, Velocity(pan=1.0))

    assert setup.driver.calls == []


# --- latched velocities (the joystick) -------------------------------------------------------


def test_a_latched_velocity_held_still_never_times_out(setup: Setup) -> None:
    # The stick is pushed once and held perfectly still: no more frames.
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), latched=True)

    for _ in range(100):  # ten seconds of motion-worker ticks
        setup.later(0.1)

    assert "stop" not in setup.driver.names()
    assert setup.published["control"]["latched"] is True


def test_a_latched_velocity_is_resent_with_a_full_ttl_every_resend_interval(setup: Setup) -> None:
    # So an agent's own 300 ms dead-man never runs out while the stick is held.
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), latched=True)
    setup.driver.clear()

    setup.later(0.1)
    setup.later(0.1)

    assert setup.driver.calls == [("move", Velocity(pan=1.0), 300)] * 2


def test_another_source_cannot_take_over_a_latched_camera(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), latched=True)
    setup.clock.advance(5.0)

    with pytest.raises(MotionRefusedError):
        setup.arbiter.move(WEB, Velocity(tilt=1.0))


def test_a_web_stop_ends_a_latched_velocity(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0), latched=True)

    setup.arbiter.stop(WEB)
    setup.later(0.2)

    assert setup.driver.names() == ["move", "stop"]


# --- shaping happens here -----------------------------------------------------------------


def test_joystick_wobble_inside_the_deadband_sends_no_motion(setup: Setup) -> None:
    result = setup.arbiter.move(JOYSTICK, Velocity(pan=0.03, tilt=-0.02))

    assert result["moving"] is False
    assert setup.driver.calls == []


def test_the_deadband_applies_to_the_joystick_only(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=0.03))

    assert setup.driver.calls == [("move", Velocity(pan=0.03), 300)]


def test_inversion_scale_and_curve_from_the_camera_settings_are_applied(setup: Setup) -> None:
    setup.camera = setup.camera.model_copy(
        update={
            "limits": MotionLimits(invert_tilt=True, pan_speed_scale=0.5),
            "speed_curve": SpeedCurve.SQUARED,
        }
    )

    setup.arbiter.move(WEB, Velocity(pan=1.0, tilt=0.5))

    assert setup.driver.calls == [("move", Velocity(pan=0.5, tilt=-0.25), 300)]


# --- presets and modes --------------------------------------------------------------------


def test_a_preset_recall_ends_the_held_velocity_and_reaches_the_driver(setup: Setup) -> None:
    setup.arbiter.move(WEB, Velocity(pan=1.0))

    setup.arbiter.recall_preset(WEB, 3)
    setup.later(0.2)

    assert setup.driver.names() == ["move", "recall_preset"]  # no resend after it


def test_a_preset_recall_is_refused_while_another_source_moves_the_camera(setup: Setup) -> None:
    setup.arbiter.move(JOYSTICK, Velocity(pan=1.0))

    with pytest.raises(MotionRefusedError):
        setup.arbiter.recall_preset(WEB, 3)


def test_preset_numbers_outside_the_cameras_range_are_refused(setup: Setup) -> None:
    with pytest.raises(MotionRefusedError, match="0 to 127"):
        setup.arbiter.store_preset(WEB, 128)


def test_a_camera_without_presets_refuses_them(fake_clock: FakeClock) -> None:
    setup = Setup(fake_clock, RecordingDriver(preset_max=None))

    with pytest.raises(MotionRefusedError, match="no presets"):
        setup.arbiter.recall_preset(WEB, 0)


def test_set_mode_is_refused_for_a_camera_the_studio_drives_directly(setup: Setup) -> None:
    with pytest.raises(MotionRefusedError, match="only agent-owned"):
        setup.arbiter.set_mode(WEB, Mode.AUTO, None)


def test_set_mode_is_passed_to_a_driver_with_modes(fake_clock: FakeClock) -> None:
    setup = Setup(fake_clock, RecordingModeDriver())

    reply = setup.arbiter.set_mode(WEB, Mode.HOLD, {"resume_after_ms": 1000}).result(1)

    assert reply.mode == Mode.HOLD
    assert setup.driver.calls == [("set_mode", Mode.HOLD, {"resume_after_ms": 1000})]


def test_every_command_records_its_source_in_the_cameras_control_state(setup: Setup) -> None:
    setup.arbiter.move(API, Velocity(pan=0.2))

    control = setup.published["control"]
    assert control["last_source"] == API
    assert control["last_command"] == "move"
    assert control["velocity"] == {"pan": 0.2, "tilt": 0.0, "zoom": 0.0}
