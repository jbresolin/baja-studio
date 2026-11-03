"""Tests for the agent-proxied driver (cameras/drivers/agent_proxy.py), on its own.

The agents module is replaced by `ScriptedGateway`: the test sets what the
agent link looks like and sees which calls were submitted. The direct
fallback is a `RecordingDriver`. The driver's follow worker runs under a
supervisor on the fake clock and looks at the link every 0.1 s, so the tests
let fake time pass with `follow_for`. (Against a real fake agent, the whole
path is tested in tests/modules/test_cameras.py.)
"""

import logging
from collections.abc import Iterator
from concurrent.futures import Future
from typing import Any

import pytest

from baja_studio.agents.contract import Mode, Status
from baja_studio.cameras.config import CameraAddress, DirectFallback
from baja_studio.cameras.driver import CameraDriver, DriverHost, Velocity
from baja_studio.cameras.drivers.agent_gateway import AgentCall, AgentLinkView
from baja_studio.cameras.drivers.agent_proxy import AgentProxyDriver
from baja_studio.core.supervisor import Supervisor
from tests.cameras.recording_driver import RecordingDriver
from tests.conftest import FakeClock, eventually

CAMERA_OK = {
    "status": "ok",
    "mode": "auto",
    "tracker": {"state": "tracking", "target_id": "car-12", "confidence": 0.9},
    "position": {"pan": 10.0, "tilt": -2.0, "zoom": 0.3},
    "moving": True,
    "last_command_source": None,
    "last_command_ts": None,
}
FALLBACK = DirectFallback(address=CameraAddress(host="10.0.0.9"))


class ScriptedGateway:
    """An agents module whose link the test sets; submitted calls are recorded and answered."""

    def __init__(self) -> None:
        self.view: AgentLinkView | None = AgentLinkView("ok", "", {"cam1": CAMERA_OK}, None)
        self.submitted: list[AgentCall] = []
        self.fallback_after = 10.0

    def link(self, agent_id: str) -> AgentLinkView | None:
        return self.view

    def submit(self, agent_id: str, call: AgentCall) -> "Future[Any]":
        self.submitted.append(call)
        done: Future[Any] = Future()
        done.set_result({"accepted": True, "mode": "manual"})
        return done

    def direct_fallback_after_s(self) -> float:
        return self.fallback_after

    def set_status(self, status: str, reason: str = "") -> None:
        cameras = {"cam1": CAMERA_OK} if status == "ok" else {}
        self.view = AgentLinkView(status, reason, cameras, None)


class Rig:
    def __init__(self, fake_clock: FakeClock, supervisor: Supervisor, fallback: bool) -> None:
        self.clock = fake_clock
        self.gateway = ScriptedGateway()
        self.state: dict[str, Any] = {}
        self.fallbacks: list[RecordingDriver] = []
        host = DriverHost(
            "trackside",
            fake_clock,
            logging.getLogger("test"),
            self.state.__setitem__,
            lambda spec: supervisor.start_worker("cameras", spec),
            lambda name: supervisor.stop_worker("cameras", name),
        )
        self.driver = AgentProxyDriver(
            host, self.gateway, "laptop-a", "cam1", FALLBACK if fallback else None, self.make
        )

    def make(self, config: DirectFallback) -> CameraDriver:
        driver = RecordingDriver()
        self.fallbacks.append(driver)
        return driver

    def follow_for(self, seconds: float) -> None:
        """Let fake time pass in 0.1 s steps; the follow worker looks once per step."""
        for _ in range(round(seconds / 0.1)):
            self.clock.advance(0.1)
            eventually(lambda: self.clock.sleeping_count >= 1)


@pytest.fixture
def supervisor(fake_clock: FakeClock) -> Iterator[Supervisor]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    yield supervisor
    supervisor.stop_all()


def make_rig(fake_clock: FakeClock, supervisor: Supervisor, fallback: bool = True) -> Rig:
    rig = Rig(fake_clock, supervisor, fallback)
    rig.driver.connect()
    eventually(lambda: rig.state.get("status") == "ok")
    return rig


# --- mirroring and health ---------------------------------------------------------------------


def test_the_cameras_state_is_mirrored_from_the_agent(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor)

    assert rig.state["mode"] == "auto"
    assert rig.state["tracker"]["target_id"] == "car-12"
    assert rig.state["route"] == "agent"
    assert rig.driver.mode() == "auto"
    position = rig.driver.position()
    assert position is not None and position.pan == 10.0


def test_the_agents_preset_names_are_mirrored_from_its_info(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # The agent keeps an agent camera's preset names; they come with /agent/info.
    rig = make_rig(fake_clock, supervisor)
    info = {
        "cameras": [
            {
                "id": "cam1",
                "kind": "ptz",
                "has_position_feedback": True,
                "has_zoom": True,
                "supports_auto": True,
                "limits": {"preset_max": 254},
                "presets": [{"n": 1, "label": "Start gate"}],
            }
        ]
    }

    rig.gateway.view = AgentLinkView("ok", "", {"cam1": CAMERA_OK}, info)
    rig.follow_for(0.1)

    assert rig.state["presets"] == [{"n": 1, "label": "Start gate"}]
    assert rig.state["capabilities"]["preset_max"] == 254


def test_renaming_a_preset_is_forwarded_to_the_agent(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor)

    rig.driver.label_preset(1, "Jump")

    assert len(rig.gateway.submitted) == 1


@pytest.mark.parametrize(
    ("link_status", "camera_status", "reason_part"),
    [
        ("degraded", Status.DEGRADED, "state stale"),
        ("offline", Status.OFFLINE, "laptop-a offline"),
        ("connecting", Status.OFFLINE, "laptop-a connecting"),
    ],
)
def test_the_cameras_health_follows_the_agent_link(
    fake_clock: FakeClock,
    supervisor: Supervisor,
    link_status: str,
    camera_status: Status,
    reason_part: str,
) -> None:
    rig = make_rig(fake_clock, supervisor)

    rig.gateway.set_status(link_status, "state stale" if link_status == "degraded" else "")
    rig.follow_for(0.1)

    assert rig.driver.health() == camera_status
    assert reason_part in rig.state["status_reason"]


def test_a_camera_the_agent_does_not_list_is_offline(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor)

    rig.gateway.view = AgentLinkView("ok", "", {"other": CAMERA_OK}, None)
    rig.follow_for(0.1)

    assert rig.state["status_reason"] == "agent laptop-a lists no camera cam1"


# --- commands ---------------------------------------------------------------------------------


def test_connecting_sends_the_agent_nothing(fake_clock: FakeClock, supervisor: Supervisor) -> None:
    rig = make_rig(fake_clock, supervisor)
    rig.follow_for(1.0)

    assert rig.gateway.submitted == []


def test_commands_are_forwarded_while_the_agent_is_ok(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor)

    rig.driver.move(Velocity(pan=0.5), 300)
    rig.driver.stop()
    rig.driver.recall_preset(2)

    assert len(rig.gateway.submitted) == 3


def test_motion_is_dropped_while_the_agent_is_unreachable(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # The agent's own ttl has stopped the camera; a backlog would replay old motion.
    rig = make_rig(fake_clock, supervisor)
    rig.gateway.set_status("offline")
    rig.follow_for(0.1)

    rig.driver.move(Velocity(pan=0.5), 300)
    rig.driver.stop()

    assert rig.gateway.submitted == []


# --- direct fallback ------------------------------------------------------------------------------


def test_the_fallback_starts_only_after_the_agent_has_been_offline_long_enough(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor)
    rig.gateway.set_status("offline")

    rig.follow_for(9.5)
    assert rig.fallbacks == []
    assert rig.driver.route == "agent"

    rig.follow_for(0.8)
    assert [driver.names() for driver in rig.fallbacks] == [["connect"]]
    assert rig.driver.route == "fallback"
    assert rig.state["route"] == "fallback"


def test_a_degraded_agent_does_not_trigger_the_fallback(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    # Degraded means "reachable but stale": the agent still owns the camera.
    rig = make_rig(fake_clock, supervisor)
    rig.gateway.set_status("degraded", "state stale")

    rig.follow_for(12)

    assert rig.fallbacks == []


def test_without_a_fallback_configured_the_camera_just_stays_offline(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = make_rig(fake_clock, supervisor, fallback=False)
    rig.gateway.set_status("offline")

    rig.follow_for(12)

    assert rig.fallbacks == []
    assert rig.driver.health() == Status.OFFLINE


def in_fallback(fake_clock: FakeClock, supervisor: Supervisor) -> Rig:
    rig = make_rig(fake_clock, supervisor)
    rig.gateway.fallback_after = 1.0
    rig.gateway.set_status("offline")
    rig.follow_for(1.3)
    assert rig.driver.route == "fallback"
    return rig


def test_in_fallback_every_command_goes_to_the_direct_driver(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = in_fallback(fake_clock, supervisor)

    rig.driver.move(Velocity(tilt=0.5), 300)
    rig.driver.stop()
    rig.driver.store_preset(1)

    assert rig.fallbacks[0].names() == ["connect", "move", "stop", "store_preset"]
    assert rig.gateway.submitted == []


def test_in_fallback_set_mode_is_refused_because_there_is_no_tracker(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = in_fallback(fake_clock, supervisor)

    refused = rig.driver.set_mode(Mode.AUTO, None)

    assert "driven directly" in str(refused.exception())


def test_in_fallback_renaming_a_preset_is_refused_because_the_agent_keeps_them(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = in_fallback(fake_clock, supervisor)

    refused = rig.driver.label_preset(1, "Jump")

    assert "driven directly" in str(refused.exception())


def test_when_the_agent_returns_the_direct_driver_is_stopped_closed_and_the_agent_resynced(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = in_fallback(fake_clock, supervisor)

    rig.gateway.set_status("ok")
    rig.follow_for(0.2)

    assert rig.driver.route == "agent"
    assert rig.fallbacks[0].names() == ["connect", "stop", "disconnect"]
    assert len(rig.gateway.submitted) == 1  # the resync


def test_disconnect_also_closes_an_open_fallback(
    fake_clock: FakeClock, supervisor: Supervisor
) -> None:
    rig = in_fallback(fake_clock, supervisor)

    rig.driver.disconnect()

    assert rig.fallbacks[0].names()[-1] == "disconnect"
