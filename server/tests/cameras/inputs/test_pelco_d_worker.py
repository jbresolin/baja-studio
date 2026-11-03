"""Tests for the joystick controller's worker (cameras/inputs/pelco_d_worker.py).

The first group calls `handle(frame)` directly: which arbiter call each
frame turns into. The second runs the real worker thread under a supervisor,
reading a `FakeSerialPort`, to check unplugging and replugging the adapter.
"""

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.cameras.config import CameraAddress, CameraConfig, ControllerConfig, MotionSettings
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.inputs import pelco_d
from baja_studio.cameras.inputs.pelco_d import FrameParser, build_frame
from baja_studio.cameras.inputs.pelco_d_worker import PelcoDInput
from baja_studio.cameras.motion.arbiter import MotionArbiter
from baja_studio.core.supervisor import HealthState, Supervisor
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES, FakeSerialPorts
from tests.cameras.recording_driver import RecordingDriver
from tests.conftest import FakeClock, eventually


class Rig:
    """One controller wired to two cameras through real arbiters.

    Camera 1 answers to address 1 (the sniffed frames' address), camera 2 to
    address 2.
    """

    def __init__(self, fake_clock: FakeClock, port_present: bool = True) -> None:
        self.motion = MotionSettings()
        self.driver, self.arbiter = self._camera(fake_clock, "cam1", 1)
        self.driver2, self.arbiter2 = self._camera(fake_clock, "cam2", 2)
        arbiters = {1: self.arbiter, 2: self.arbiter2}
        self.published: dict[str, Any] = {}
        self.logged: list[tuple[str, dict[str, Any], str]] = []
        self.ports = FakeSerialPorts()
        if port_present:
            self.port = self.ports.add("COM8")
        self.input = PelcoDInput(
            ControllerConfig(id="ctl1", port="COM8"),
            arbiter_for_address=arbiters.get,
            motion=lambda: self.motion,
            publish=self.published.__setitem__,
            log_command=lambda command, args, result: self.logged.append((command, args, result)),
            clock=fake_clock,
            logger=logging.getLogger("test"),
            opener=self.ports.open,
        )

    def _camera(
        self, fake_clock: FakeClock, camera_id: str, address: int
    ) -> tuple[RecordingDriver, MotionArbiter]:
        driver = RecordingDriver()
        camera = CameraConfig(
            id=camera_id, address=CameraAddress(host="10.0.0.9"), controller_address=address
        )
        arbiter = MotionArbiter(
            driver,
            config=lambda: camera,
            motion=lambda: self.motion,
            clock=fake_clock,
            publish=lambda key, value: None,
            logger=logging.getLogger("test"),
        )
        return driver, arbiter

    def press(self, *names: str) -> None:
        """Feed sniffed frames, by fixture name, straight into `handle`."""
        for name in names:
            for frame in FrameParser().feed(SNIFFED_FRAMES[name]):
                self.input.handle(frame)


@pytest.fixture
def rig(fake_clock: FakeClock) -> Rig:
    return Rig(fake_clock)


# --- frames to arbiter calls ---------------------------------------------------------------


def test_a_full_deflection_right_moves_the_camera_right_at_full_speed(rig: Rig) -> None:
    rig.press("pan_right_full")

    # Full deflection is still 1.0 after the deadband is taken out.
    assert rig.driver.calls == [("move", Velocity(pan=1.0), 300)]


def test_a_half_deflection_moves_slower_than_a_full_one(rig: Rig) -> None:
    rig.press("pan_right_half")
    half = rig.driver.calls[-1][1].pan
    rig.press("stop", "pan_right_full")
    full = rig.driver.calls[-1][1].pan

    assert 0 < half < full


def test_the_zoom_buttons_zoom_at_the_configured_fixed_speed(rig: Rig) -> None:
    rig.press("zoom_in")
    rig.press("stop", "zoom_out")

    zooms = [call[1].zoom for call in rig.driver.calls if call[0] == "move"]
    assert zooms == [0.6, -0.6]


def test_a_repeated_frame_sends_nothing_new(rig: Rig) -> None:
    rig.press("tilt_up_full", "tilt_up_full", "tilt_up_full")

    assert rig.driver.names() == ["move"]


# --- the latch: a move holds until the controller says something else ---------------------


def test_a_stick_held_still_never_times_out(rig: Rig, fake_clock: FakeClock) -> None:
    # One frame, then nothing: a controller that does not repeat while the
    # stick is held. The camera keeps moving, re-sent by the arbiter.
    rig.press("pan_right_full")

    for _ in range(100):  # ten seconds of motion-worker ticks
        fake_clock.advance(0.1)
        rig.arbiter.tick()

    assert "stop" not in rig.driver.names()
    assert rig.driver.names() == ["move"] * 101  # the first, then one every tick


def test_a_different_frame_releases_the_latch(rig: Rig) -> None:
    rig.press("pan_right_full", "call_2")

    assert rig.driver.names() == ["move", "stop"]


def test_a_new_direction_replaces_the_latched_velocity_without_a_stop(rig: Rig) -> None:
    rig.press("pan_right_full", "pan_left_full")

    assert rig.driver.names() == ["move", "move"]
    assert rig.driver.calls[-1][1].pan < 0


def test_a_frame_for_another_camera_stops_the_latched_one(rig: Rig) -> None:
    rig.press("pan_right_full")

    # The operator selects camera 2 and pushes the stick.
    rig.input.handle(FrameParser().feed(build_frame(2, pelco_d.PAN_LEFT, 0x3F, 0x00))[0])

    assert rig.driver.names() == ["move", "stop"]
    assert rig.driver2.names() == ["move"]


def test_an_idle_controllers_repeated_stops_leave_a_camera_the_web_owns_alone(
    rig: Rig,
) -> None:
    # The web page drives camera 1; the desk sits at rest repeating "stop".
    rig.arbiter.move("web:browser-1", Velocity(pan=0.5), ttl_ms=2000)
    rig.driver.clear()

    rig.press("stop", "stop", "stop")

    assert rig.driver.calls == []
    assert rig.logged == []


def test_with_silence_detection_off_a_quiet_controller_keeps_its_latch(
    rig: Rig, fake_clock: FakeClock
) -> None:
    rig.press("pan_right_full")

    fake_clock.advance(30)
    rig.input.check_silence()

    assert "stop" not in rig.driver.names()


def test_with_silence_detection_on_a_quiet_controller_stops_its_camera(
    rig: Rig, fake_clock: FakeClock
) -> None:
    rig.motion = MotionSettings(controller_silence_ms=2000)
    rig.press("pan_right_full")

    fake_clock.advance(1.9)
    rig.input.check_silence()
    assert "stop" not in rig.driver.names()

    fake_clock.advance(0.1)
    rig.input.check_silence()
    assert rig.driver.names()[-1] == "stop"


def test_every_stop_after_motion_is_a_full_stop_even_if_the_last_frame_was_a_stop(
    rig: Rig,
) -> None:
    # stop, zoom in, stop: the second stop must not be dropped as "same as before"
    # (the zoom runaway ptz_test.py fixed).
    rig.press("stop", "zoom_in", "stop")

    assert rig.driver.names() == ["move", "stop"]


def test_stop_frames_while_nothing_moves_send_nothing(rig: Rig) -> None:
    rig.press("stop", "stop", "stop")

    assert rig.driver.calls == []


def test_a_preset_button_recalls_the_zero_based_preset(rig: Rig) -> None:
    rig.press("recall_preset_1")

    assert rig.driver.calls == [("recall_preset", 0)]


def test_the_call_button_is_published_for_the_ui_and_moves_nothing(rig: Rig) -> None:
    rig.press("call_2")

    assert rig.published["last_call"]["n"] == 2
    assert rig.driver.calls == []
    assert rig.logged == [("call", {"n": 2}, "ok")]


def test_a_frame_for_an_address_with_no_camera_is_logged_once_a_minute(
    rig: Rig, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    unrouted = bytes.fromhex("FF 05 00 02 3F 00 46")  # pan right, address 5

    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            rig.input.handle(FrameParser().feed(unrouted)[0])
        fake_clock.advance(60)
        rig.input.handle(FrameParser().feed(unrouted)[0])

    warnings = [record for record in caplog.records if "address 5" in record.getMessage()]
    assert len(warnings) == 2


def test_the_command_log_gets_the_start_of_a_move_and_the_stop_not_every_speed_change(
    rig: Rig,
) -> None:
    rig.press("pan_right_half", "pan_right_full", "stop")

    assert [entry[0] for entry in rig.logged] == ["move", "stop"]
    assert rig.logged[0][1]["camera"] == "cam1"


# --- the worker and the serial port --------------------------------------------------------


@pytest.fixture
def supervisor(fake_clock: FakeClock) -> Iterator[Supervisor]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    yield supervisor
    supervisor.stop_all()


def worker_state(supervisor: Supervisor) -> HealthState:
    return supervisor.module_health("cameras").state


def test_the_worker_reads_frames_from_the_port_and_moves_the_camera(
    rig: Rig, supervisor: Supervisor
) -> None:
    supervisor.start_worker("cameras", rig.input.worker_spec())
    eventually(lambda: worker_state(supervisor) == HealthState.OK)

    # The frame arrives in two pieces, as it may from a real port.
    rig.port.feed(SNIFFED_FRAMES["tilt_down_full"][:4])
    rig.port.feed(SNIFFED_FRAMES["tilt_down_full"][4:])

    eventually(lambda: rig.driver.names() == ["move"])
    assert rig.port.baud == 2400


def test_a_missing_port_is_offline_and_is_found_when_it_appears(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = Rig(fake_clock, port_present=False)  # no COM8 at all yet
    supervisor.start_worker("cameras", rig.input.worker_spec())
    eventually(lambda: "cannot open COM8" in rig.published.get("status_reason", ""))
    assert worker_state(supervisor) == HealthState.OFFLINE

    rig.ports.add("COM8")
    eventually(lambda: fake_clock.sleeping_count >= 1)
    fake_clock.advance(1.0)  # the worker tries again once a second

    eventually(lambda: worker_state(supervisor) == HealthState.OK)


def test_losing_the_serial_link_stops_only_the_cameras_the_joystick_owns(
    rig: Rig, supervisor: Supervisor
) -> None:
    supervisor.start_worker("cameras", rig.input.worker_spec())
    eventually(lambda: worker_state(supervisor) == HealthState.OK)
    rig.port.feed(SNIFFED_FRAMES["pan_right_full"])  # the joystick drives camera 1
    eventually(lambda: rig.driver.names() == ["move"])
    rig.arbiter2.move("web:browser-1", Velocity(tilt=0.5), ttl_ms=2000)  # the web, camera 2

    rig.port.unplug()

    eventually(lambda: rig.driver.names() == ["move", "stop"])
    assert rig.driver2.names() == ["move"]
    assert ("stop", {"camera": "cam1", "reason": "serial link lost"}, "ok") in rig.logged


def test_stopping_the_controller_stops_the_camera_it_latched(
    rig: Rig, supervisor: Supervisor
) -> None:
    # The controller is removed from the settings while its stick is held.
    supervisor.start_worker("cameras", rig.input.worker_spec())
    eventually(lambda: worker_state(supervisor) == HealthState.OK)
    rig.port.feed(SNIFFED_FRAMES["zoom_in"])
    eventually(lambda: rig.driver.names() == ["move"])

    supervisor.stop_worker("cameras", rig.input.worker_name)

    assert rig.driver.names() == ["move", "stop"]


def test_unplugging_the_adapter_is_offline_and_replugging_it_is_ok_without_a_restart(
    rig: Rig, supervisor: Supervisor, fake_clock: FakeClock
) -> None:
    supervisor.start_worker("cameras", rig.input.worker_spec())
    eventually(lambda: worker_state(supervisor) == HealthState.OK)

    rig.port.unplug()
    eventually(lambda: worker_state(supervisor) == HealthState.OFFLINE)
    assert "COM8 lost" in rig.published["status_reason"]

    rig.port.plug_in()
    eventually(lambda: fake_clock.sleeping_count >= 1)
    fake_clock.advance(1.0)

    eventually(lambda: worker_state(supervisor) == HealthState.OK)
    assert rig.port.open_count == 2  # reopened by the worker itself
