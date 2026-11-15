"""Tests for the VISCA/IP driver (cameras/drivers/visca_ip.py) with its real worker thread.

The driver runs under a supervisor on the fake clock and talks to a
`FakeViscaCamera` through `InProcessDatagramTransport`: no socket is opened.
Time moves in small steps (`advance_in_steps`) so the worker wakes, sends and
reads as it would in real time.
"""

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.agents.contract import Status
from baja_studio.cameras.driver import DriverHost, Velocity
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_ip import ViscaIpDriver
from baja_studio.core.supervisor import HealthState, Supervisor
from baja_studio.fakes.fake_visca_camera import FakeViscaCamera, InProcessDatagramTransport
from baja_studio.fakes.fake_visca_measured import MeasuredReplies
from tests.conftest import FakeClock, eventually, module_worker_threads, step_fake_time

STEP_SECONDS = 0.05


def advance_in_steps(fake_clock: FakeClock, seconds: float) -> None:
    """Let `seconds` pass in 50 ms steps, letting the driver's worker run each step."""
    step_fake_time(fake_clock, seconds, module_worker_threads("cameras"), STEP_SECONDS)


class Bench:
    """A fake camera, the pretend network to it, and a driver under a supervisor."""

    def __init__(self, fake_clock: FakeClock, supervisor: Supervisor) -> None:
        self.clock = fake_clock
        self.supervisor = supervisor
        self.camera = FakeViscaCamera()
        self.transports: list[InProcessDatagramTransport] = []
        self.unplugged = False
        self.state: dict[str, Any] = {}
        host = DriverHost(
            camera_id="cam1",
            clock=fake_clock,
            logger=logging.getLogger("test"),
            publish=self.state.__setitem__,
            start_worker=lambda spec: supervisor.start_worker("cameras", spec),
            stop_worker=lambda name: supervisor.stop_worker("cameras", name),
        )
        # The camera's limits.pan_speed_max_step / tilt_speed_max_step; tests change them.
        self.top_speed_steps = (0x18, 0x14)
        # The camera's position_poll_seconds setting; tests change it.
        self.position_poll_seconds = 1.0
        self.driver = ViscaIpDriver(
            host,
            "10.0.0.9",
            52381,
            resend_interval_seconds=lambda: 0.1,
            opener=self.open,
            top_speed_steps=lambda: self.top_speed_steps,
            position_poll_seconds=lambda: self.position_poll_seconds,
        )

    def open(self, address: str, port: int) -> InProcessDatagramTransport:
        """The driver's opener: a new pretend socket to the fake camera."""
        transport = InProcessDatagramTransport(self.camera, self.clock.monotonic)
        transport.unplugged = self.unplugged
        self.transports.append(transport)
        return transport

    def unplug(self, unplugged: bool = True) -> None:
        self.unplugged = unplugged
        for transport in self.transports:
            transport.unplugged = unplugged

    def health(self) -> HealthState:
        return self.supervisor.module_health("cameras").state

    def sent_times(self) -> list[float]:
        """When each datagram the driver sent left, on the fake clock (all sockets)."""
        return [moment for transport in self.transports for moment in transport.sent_at]


@pytest.fixture
def bench(fake_clock: FakeClock) -> Iterator[Bench]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    bench = Bench(fake_clock, supervisor)
    yield bench
    supervisor.stop_all()


def connected(bench: Bench) -> Bench:
    bench.driver.connect()
    eventually(lambda: bench.health() == HealthState.OK)
    return bench


def test_the_driver_is_offline_until_the_camera_answers_then_ok(bench: Bench) -> None:
    bench.driver.connect()

    # The first poll goes out at once and the fake answers it.
    eventually(lambda: bench.health() == HealthState.OK)
    assert bench.state["status"] == "ok"
    assert bench.driver.health() == Status.OK


def test_connecting_sends_no_motion_and_no_stop(bench: Bench) -> None:
    connected(bench)
    advance_in_steps(bench.clock, 1.0)

    # Only the once-a-second position inquiries have reached the camera.
    inquiries = {packets.pan_tilt_position_inquiry(), packets.zoom_position_inquiry()}
    assert set(bench.camera.received) == inquiries


def test_full_speed_uses_the_cameras_top_speed_steps_read_live(bench: Bench) -> None:
    connected(bench)
    bench.top_speed_steps = (18, 17)  # ptz_test.py's steps, set in the camera's limits

    bench.driver.move(Velocity(pan=1.0, tilt=1.0), ttl_ms=1000)
    eventually(lambda: bench.camera.moving)
    bench.top_speed_steps = (0x18, 0x14)  # edited while moving: the next resend uses it
    advance_in_steps(bench.clock, 0.1)

    full = Velocity(pan=1.0, tilt=1.0)
    assert packets.pan_tilt_for(full, 18, 17) in bench.camera.received
    assert packets.pan_tilt_for(full, 0x18, 0x14) in bench.camera.received


def test_a_move_reaches_the_camera_and_is_stopped_when_its_ttl_runs_out(bench: Bench) -> None:
    connected(bench)

    bench.driver.move(Velocity(pan=1.0), ttl_ms=300)
    eventually(lambda: bench.camera.moving)
    advance_in_steps(bench.clock, 0.4)

    assert not bench.camera.moving
    assert packets.pan_tilt_stop() in bench.camera.received


def test_position_is_read_back_and_published(bench: Bench) -> None:
    bench.camera.pan.position = 200
    connected(bench)

    eventually(lambda: bench.state.get("position") is not None)
    assert bench.state["position"]["pan"] == 200 * packets.DEGREES_PER_PAN_TILT_STEP


def test_dropped_replies_make_the_camera_degraded_then_offline_then_ok_again(
    bench: Bench,
) -> None:
    connected(bench)
    bench.camera.dropping_replies = True
    seen: list[HealthState] = []

    # One inquiry a second goes unanswered: 3 misses take ~3 s, 10 take ~10 s.
    for _ in range(12):
        advance_in_steps(bench.clock, 1.0)
        seen.append(bench.health())
    assert HealthState.DEGRADED in seen
    assert seen[-1] == HealthState.OFFLINE

    bench.camera.dropping_replies = False
    advance_in_steps(bench.clock, 1.1)

    assert bench.health() == HealthState.OK


def test_while_offline_the_socket_is_reopened(bench: Bench) -> None:
    connected(bench)
    bench.unplug()

    advance_in_steps(bench.clock, 12.0)

    assert bench.health() == HealthState.OFFLINE
    assert len(bench.transports) >= 2
    assert bench.transports[0].closed


def test_no_runaway_when_the_stick_is_released_during_an_outage(bench: Bench) -> None:
    # The stick is held, the cable is pulled, the stick is released, the
    # cable goes back in: the camera must end up stopped.
    connected(bench)
    bench.driver.move(Velocity(pan=1.0), ttl_ms=5000)
    eventually(lambda: bench.camera.moving)

    bench.unplug()
    advance_in_steps(bench.clock, 0.2)
    bench.driver.stop()
    advance_in_steps(bench.clock, 0.5)
    assert bench.camera.moving  # it never heard the stop

    # Plugged back in: the next poll (once a second) is answered, and the
    # stop that never arrived is sent again.
    bench.unplug(False)
    advance_in_steps(bench.clock, 1.5)

    assert not bench.camera.moving


def test_a_host_name_that_cannot_be_opened_is_offline_not_a_crash(
    bench: Bench, fake_clock: FakeClock
) -> None:
    def refuse(address: str, port: int) -> InProcessDatagramTransport:
        raise OSError("Name or service not known")

    driver = ViscaIpDriver(
        DriverHost(
            "cam2",
            fake_clock,
            logging.getLogger("test"),
            lambda key, value: None,
            lambda spec: bench.supervisor.start_worker("cameras", spec),
            lambda name: bench.supervisor.stop_worker("cameras", name),
        ),
        "no-such-camera.invalid",
        52381,
        resend_interval_seconds=lambda: 0.1,
        opener=refuse,
    )

    driver.connect()

    eventually(lambda: "cannot open" in bench.supervisor.module_health("cameras").message)
    assert bench.health() == HealthState.OFFLINE


def test_disconnect_stops_the_worker_and_closes_the_socket(bench: Bench) -> None:
    connected(bench)

    bench.driver.disconnect()

    assert bench.supervisor.worker_names("cameras") == []
    assert bench.transports[-1].closed


def test_settled_is_true_only_once_a_stop_has_been_sent(bench: Bench) -> None:
    connected(bench)
    bench.driver.move(Velocity(pan=1.0), ttl_ms=5000)
    assert not bench.driver.settled()

    bench.driver.stop()
    eventually(lambda: bench.driver.settled())


# --- the bench PTZ: the fake in its measured mode ---------------------------------------------


def measured(bench: Bench) -> Bench:
    """Make the bench's camera answer like the real PTZ, and connect past its silent start."""
    bench.camera.measured = MeasuredReplies()
    bench.driver.connect()
    advance_in_steps(bench.clock, 5.0)
    return bench


def most_packets_in_one_second(times: list[float], start: float, end: float) -> int:
    """The busiest one-second window (sliding by 50 ms) between `start` and `end`."""
    windows = range(round((end - start - 1.0) / STEP_SECONDS) + 1)
    return max(
        sum(1 for moment in times if begin <= moment < begin + 1.0 - 1e-9)
        for begin in (start + window * STEP_SECONDS for window in windows)
    )


def test_at_rest_the_driver_sends_the_measured_camera_one_packet_a_second(bench: Bench) -> None:
    # The bench bug: ~14 packets a second at rest froze the camera's video.
    # Now, with the default poll of one inquiry a second, that is all it gets.
    measured(bench)
    start = bench.clock.monotonic()

    advance_in_steps(bench.clock, 20.0)

    times = bench.sent_times()
    assert most_packets_in_one_second(times, start, start + 20.0) <= 1
    assert bench.driver.health() == Status.OK


def test_a_stop_to_the_measured_camera_is_a_short_burst_and_then_quiet_again(
    bench: Bench,
) -> None:
    # The stop is sent three times (a pan-tilt stop and a zoom stop each),
    # 100 ms apart, and never again: the old driver resent it forever.
    measured(bench)
    bench.driver.move(Velocity(pan=0.5), ttl_ms=5000)
    advance_in_steps(bench.clock, 1.0)
    sent_before_the_stop = len(bench.sent_times())
    bench.driver.stop()
    stopped_at = bench.clock.monotonic()

    advance_in_steps(bench.clock, 20.0)

    times = bench.sent_times()[sent_before_the_stop:]
    stops = [payload for payload in bench.camera.received if payload == packets.pan_tilt_stop()]
    assert len(stops) == 3
    # The second with the stop: six stop packets and at most one inquiry.
    assert most_packets_in_one_second(times, stopped_at, stopped_at + 1.0) <= 7
    # Every second after it: back to one inquiry.
    assert most_packets_in_one_second(times, stopped_at + 1.0, stopped_at + 20.0) <= 1
    assert not bench.camera.moving
    assert bench.driver.settled()


def test_the_measured_camera_stays_ok_and_its_position_is_published(bench: Bench) -> None:
    bench.camera.pan.position = 0x01A5
    measured(bench)
    seen = set()

    for _ in range(60):
        advance_in_steps(bench.clock, 1.0)
        seen.add(bench.health())

    assert seen == {HealthState.OK}
    assert bench.state["status"] == "ok"
    # At rest its occasional pan/tilt answers are read, whatever their number.
    assert bench.state["position"]["pan"] == 0x01A5 * packets.DEGREES_PER_PAN_TILT_STEP


def test_the_measured_camera_s_position_is_unknown_while_it_moves(bench: Bench) -> None:
    # It answers no position while moving, and its late answers may predate
    # the move: the page is shown no position rather than an old one.
    measured(bench)
    advance_in_steps(bench.clock, 60.0)
    assert bench.state["position"] is not None

    bench.driver.move(Velocity(pan=0.5), ttl_ms=5000)
    advance_in_steps(bench.clock, 0.2)

    assert bench.state["position"] is None
    assert bench.driver.health() == Status.OK


def test_the_driver_logs_once_that_the_camera_does_not_echo_sequence_numbers(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="test")

    measured(bench)
    advance_in_steps(bench.clock, 5.0)

    lines = [record.getMessage() for record in caplog.records if "echo" in record.getMessage()]
    assert lines == ["camera cam1 does not echo VISCA sequence numbers; judging it by any reply"]


def test_a_poll_setting_of_zero_leaves_only_a_check_every_three_seconds(bench: Bench) -> None:
    bench.position_poll_seconds = 0.0  # "set 0 if the camera's video stutters"
    measured(bench)
    start = bench.clock.monotonic()

    advance_in_steps(bench.clock, 30.0)

    sent = [moment for moment in bench.sent_times() if moment >= start]
    assert 9 <= len(sent) <= 11  # one every 3 s
    # Its ACKs and completions keep it ok; no position is published at all.
    assert bench.driver.health() == Status.OK
    assert bench.state["position"] is None
