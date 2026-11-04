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
        self.driver = ViscaIpDriver(
            host,
            "10.0.0.9",
            52381,
            resend_interval_seconds=lambda: 0.1,
            opener=self.open,
            top_speed_steps=lambda: self.top_speed_steps,
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

    # Only the twice-a-second position inquiries have reached the camera.
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

    # Two inquiries every 0.5 s go unanswered: 3 misses take ~1.5 s, 10 take ~5 s.
    for _ in range(12):
        advance_in_steps(bench.clock, 0.5)
        seen.append(bench.health())
    assert HealthState.DEGRADED in seen
    assert seen[-1] == HealthState.OFFLINE

    bench.camera.dropping_replies = False
    advance_in_steps(bench.clock, 0.6)

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

    bench.unplug(False)
    advance_in_steps(bench.clock, 0.3)

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


def test_settled_is_true_only_once_a_stop_has_been_answered(bench: Bench) -> None:
    connected(bench)
    bench.driver.move(Velocity(pan=1.0), ttl_ms=5000)
    assert not bench.driver.settled()

    bench.driver.stop()
    eventually(lambda: bench.driver.settled())
