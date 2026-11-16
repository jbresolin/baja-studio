"""Tests for one remote OBS connection (cameras/remote_obs_connection.py, WP13, ADR 0021).

A trackside laptop runs OBS; the studio keeps one connection to it, shared
by that laptop's crop cameras. Here the "laptop" is a fake OBS on the
in-process network (no port is opened), and the connection's worker runs
under a supervisor on the fake clock, as in the service. We look at what the
cameras would see through it: `link()` (connected or why not), the events
it hands to listeners, and how many connections the fake OBS has open.
"""

import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import EventSubscription
from baja_studio.agents.contract import Status
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST
from baja_studio.cameras.remote_obs_connection import (
    KEEPALIVE_INTERVAL_SECONDS,
    REMOTE_EVENT_SUBSCRIPTIONS,
    RemoteObsConnection,
)
from baja_studio.core.supervisor import Supervisor
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock, eventually, module_worker_threads

HOST, PORT, PASSWORD = "laptop.test", 4455, "laptop-secret"
WEBCAM, FILTER = "Laptop cam A", "Baja crop"
STEP_SECONDS = 0.1
WORKERS = module_worker_threads("cameras")


class Laptop:
    """A laptop's fake OBS, the studio's connection to it, and fake time."""

    def __init__(self, fake_clock: FakeClock, supervisor: Supervisor, password: str) -> None:
        self.clock = fake_clock
        self.supervisor = supervisor
        self.obs = FakeObs(fake_clock, password=PASSWORD)
        self.obs.add_video_input(WEBCAM, 3840, 2160)
        self.obs.add_crop_filter(WEBCAM, FILTER)
        self.network = InProcessObsNetwork()
        self.network.plug(HOST, PORT, self.obs)
        self.connection = RemoteObsConnection(
            HOST, PORT, password, fake_clock, self.network.connect, logging.getLogger("test")
        )
        # Everything the connection hands to its listeners (the crop cameras).
        self.heard: list[tuple[str, dict[str, Any]]] = []
        self.connection.add_listener(lambda kind, data: self.heard.append((kind, data)))

    def start(self) -> "Laptop":
        self.supervisor.start_worker("cameras", self.connection.worker_spec())
        return self

    def settle(self, answers_held: bool = False) -> None:
        """The worker asleep on the fake clock, and (unless held) every request answered."""
        client = self.connection.obs_client
        eventually(
            lambda: (
                self.clock.all_asleep_or_gone(WORKERS())
                and (answers_held or client.requests_in_flight == 0)
            )
        )

    def soon(
        self, condition: Callable[[], bool], within_seconds: float = 6.0, held: bool = False
    ) -> None:
        """Let fake time pass in 0.1 s steps until `condition()` holds."""
        for _ in range(round(within_seconds / STEP_SECONDS)):
            self.settle(held)
            if condition():
                return
            self.clock.advance(STEP_SECONDS)
        self.settle(held)
        assert condition(), "it never happened"

    def pass_time(self, seconds: float) -> None:
        """Let `seconds` of fake time pass in 0.1 s steps, the worker running each one."""
        for _ in range(round(seconds / STEP_SECONDS)):
            self.settle()
            self.clock.advance(STEP_SECONDS)
        self.settle()

    def connected(self) -> bool:
        return self.connection.link().connected


@pytest.fixture
def supervisor(fake_clock: FakeClock) -> Iterator[Supervisor]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    yield supervisor
    supervisor.stop_all()


def laptop(fake_clock: FakeClock, supervisor: Supervisor, password: str = PASSWORD) -> Laptop:
    return Laptop(fake_clock, supervisor, password).start()


# --- connecting ----------------------------------------------------------------------------------


def test_before_connecting_it_says_it_is_connecting_naming_the_laptop(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # Not started yet: nothing has been tried.
    view = Laptop(fake_clock, supervisor, PASSWORD).connection.link()

    assert (view.connected, view.reason, view.where) == (
        False,
        "connecting to OBS at laptop.test:4455",
        "laptop.test:4455",
    )


def test_it_connects_and_hands_the_laptops_events_to_its_listeners(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    # Someone edits the crop filter in the laptop's OBS: the cameras hear of it.
    remote.obs.edit_filter_by_hand(WEBCAM, FILTER, left=100)
    eventually(lambda: any(kind == "SourceFilterSettingsChanged" for kind, _ in remote.heard))


def test_it_asks_the_laptop_only_for_filter_and_input_events(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # A radio link should carry only what crop cameras use (no scene or media traffic).
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    session = remote.obs._sessions[0]
    assert session.event_subscriptions == REMOTE_EVENT_SUBSCRIPTIONS
    assert REMOTE_EVENT_SUBSCRIPTIONS == EventSubscription.FILTERS | EventSubscription.INPUTS


def test_a_wrong_password_is_degraded_not_offline(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor, password="wrong")

    remote.soon(lambda: "refused" in remote.connection.link().reason)

    view = remote.connection.link()
    # Degraded: the laptop's OBS is there and answering; our settings are wrong.
    assert view.status == Status.DEGRADED
    assert view.reason == "OBS at laptop.test:4455 refused the password (check it on Camera setup)"
    assert view.problem == view.reason  # a camera shows it as it is: it names the laptop


def test_no_password_for_an_obs_that_wants_one_says_to_set_it(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor, password="")

    remote.soon(lambda: "password" in remote.connection.link().reason)

    assert remote.connection.link().reason == (
        "OBS at laptop.test:4455 asks for a password: set it on Camera setup"
    )


def test_an_unreachable_laptop_is_offline_and_logged_once_however_often_it_retries(
    fake_clock: FakeClock, supervisor: Supervisor, caplog: pytest.LogCaptureFixture
) -> None:
    remote = Laptop(fake_clock, supervisor, PASSWORD)
    remote.network.unplug(HOST, PORT)  # the laptop is off

    with caplog.at_level(logging.DEBUG, logger="test"):
        remote.start()
        remote.pass_time(20.0)

    view = remote.connection.link()
    assert (view.connected, view.status) == (False, Status.OFFLINE)
    assert view.reason == "OBS at laptop.test:4455 unreachable: connection refused"
    # It retried many times in 20 s (0.5, 1, 2, then every 4 s), but said so once.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert len([r for r in caplog.records if r.levelno == logging.DEBUG]) >= 4


def test_a_new_password_reconnects_with_it(fake_clock: FakeClock, supervisor: Supervisor) -> None:
    remote = laptop(fake_clock, supervisor, password="wrong")
    remote.soon(lambda: "refused" in remote.connection.link().reason)

    # The operator saves the right password on Camera setup.
    remote.connection.set_password(PASSWORD)

    remote.soon(remote.connected)


def test_changing_the_password_while_connected_redials_and_tells_the_cameras(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    remote.connection.set_password("another")
    remote.soon(lambda: "refused" in remote.connection.link().reason)

    # The cameras were told the connection they used is gone (they read OBS
    # afresh when it is back), even though the studio closed it, not OBS.
    assert (CONNECTION_LOST, {"reason": "reconnecting to OBS at laptop.test:4455"}) in remote.heard


# --- losing the laptop ---------------------------------------------------------------------------


def test_obs_quitting_is_told_to_the_cameras_and_it_reconnects_when_obs_is_back(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    remote.network.unplug(HOST, PORT)
    remote.soon(lambda: not remote.connected())
    assert any(kind == CONNECTION_LOST for kind, _ in remote.heard)
    assert remote.connection.link().reason.startswith("OBS at laptop.test:4455 ")

    remote.network.plug(HOST, PORT, remote.obs)
    remote.soon(remote.connected)
    assert remote.obs.session_count == 1


def test_a_laptop_that_stops_answering_is_noticed_by_the_keepalive(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # A pulled cable closes nothing; only a question that gets no answer shows it.
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    remote.network.hold_answers(HOST, PORT)
    remote.soon(lambda: not remote.connected(), within_seconds=8.0, held=True)

    assert remote.connection.link().reason in (
        "OBS at laptop.test:4455 stopped answering; reconnecting",
        "OBS at laptop.test:4455 unreachable: did not finish the handshake in time",
    )
    assert any(kind == CONNECTION_LOST for kind, _ in remote.heard)

    # The cable is back: it redials and connects.
    remote.network.release_answers(HOST, PORT)
    remote.soon(remote.connected, within_seconds=10.0)


def test_at_rest_it_only_asks_obs_its_version_every_few_seconds(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)
    before = len(remote.obs.requests)

    remote.pass_time(10.0)

    asked = [record.request_type for record in remote.obs.requests[before:]]
    assert set(asked) == {"GetVersion"}
    assert len(asked) == pytest.approx(10 / KEEPALIVE_INTERVAL_SECONDS, abs=1)


def test_stopping_its_worker_closes_the_connection(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)
    remote.soon(remote.connected)

    supervisor.stop_worker("cameras", remote.connection.worker_name)

    assert remote.obs.session_count == 0


def test_a_listener_that_fails_does_not_stop_the_others(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    remote = laptop(fake_clock, supervisor)

    def broken(kind: str, data: dict[str, Any]) -> None:
        raise RuntimeError("a camera's listener has a bug")

    remote.connection.add_listener(broken)
    later: list[str] = []
    remove = remote.connection.add_listener(lambda kind, data: later.append(kind))
    remote.soon(remote.connected)

    remote.obs.edit_filter_by_hand(WEBCAM, FILTER, left=50)
    eventually(lambda: "SourceFilterSettingsChanged" in later)

    # And a removed listener hears nothing more.
    remove()
    count = len(later)
    remote.obs.edit_filter_by_hand(WEBCAM, FILTER, left=60)
    eventually(lambda: len(remote.heard) >= 2)
    assert len(later) == count


def test_an_ipv6_laptop_is_named_with_brackets(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    connection = RemoteObsConnection(
        "fe80::1", PORT, "", fake_clock, InProcessObsNetwork().connect, logging.getLogger("t")
    )

    assert connection.where == "[fe80::1]:4455"
    assert connection.worker_name == "remote-obs:[fe80::1]:4455"
