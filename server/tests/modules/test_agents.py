"""Tests for the agents module (modules/agents).

Each test runs the service with only the agents module, on the fake clock,
and fake agents reached through the in-process transport (no network). The
module's state (`state.agents.agents.<id>`) is what the Agents page shows,
so most checks read it.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.agents.client import AgentClient
from baja_studio.agents.contract import Mode
from baja_studio.core.module import CommandError
from baja_studio.core.services import StudioServices
from baja_studio.core.settings import SECRET_MASK
from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.in_process import InProcessTransport
from baja_studio.modules.agents import AgentsModule
from tests.conftest import AppFactory, FakeClock, eventually
from tests.fake_agents import AGENT_HOST, DELIVERY_SLACK_SECONDS, StartAgent, advance_in_steps


@pytest.fixture
def services(
    make_app: AppFactory, transport: InProcessTransport, start_agent: StartAgent
) -> Iterator[StudioServices]:
    """A running app with just the agents module, using the pretend network.

    It asks for `start_agent` only so that pytest stops the app (and its
    links) before it stops the fake agents, the way round a real studio
    would see it.
    """
    app = make_app(lambda registry: [AgentsModule(transport, studio_id="test-studio")])
    services: StudioServices = app.state.services
    with TestClient(app):
        yield services


def run(services: StudioServices, command: str, **args: Any) -> Any:
    """Run an agents command as the web page would."""
    return services.registry.dispatch_command("agents", command, args, "web:test")


def agent_state(services: StudioServices, agent_id: str) -> Any:
    return services.state.get("agents", f"agents.{agent_id}")


def status_of(services: StudioServices, agent_id: str) -> Any:
    state = agent_state(services, agent_id)
    return state and state["status"]


def test_an_added_agent_shows_ok_with_its_cameras(
    services: StudioServices, start_agent: StartAgent
) -> None:
    start_agent(ptz_cameras=2)

    run(services, "add", host=AGENT_HOST, id="laptop-a")

    eventually(lambda: status_of(services, "laptop-a") == "ok")
    eventually(lambda: set(agent_state(services, "laptop-a")["cameras"]) == {"cam1", "cam2"})
    info = agent_state(services, "laptop-a")["info"]
    assert [camera["label"] for camera in info["cameras"]] == ["Fake PTZ 1", "Fake PTZ 2"]
    assert services.supervisor.module_health("agents").state == HealthState.OK


def test_a_killed_agent_is_offline_within_five_seconds_and_back_with_new_cameras(
    services: StudioServices,
    start_agent: StartAgent,
    transport: InProcessTransport,
    fake_clock: FakeClock,
) -> None:
    start_agent(ptz_cameras=2)
    run(services, "add", host=AGENT_HOST, id="laptop-a")
    eventually(lambda: status_of(services, "laptop-a") == "ok")

    # Kill the agent. Five seconds after its last message, the page and the
    # home card say so.
    transport.unplug(AGENT_HOST, 8890)
    advance_in_steps(fake_clock, 5 + DELIVERY_SLACK_SECONDS)
    eventually(lambda: status_of(services, "laptop-a") == "offline")
    health = services.supervisor.module_health("agents")
    assert (health.state, health.message) == (
        HealthState.OFFLINE,
        "all agents away: laptop-a offline",
    )

    # Restart it with three cameras: ok again, with the new camera list.
    start_agent(ptz_cameras=3)
    advance_in_steps(fake_clock, 4)
    eventually(lambda: status_of(services, "laptop-a") == "ok")
    eventually(lambda: len(agent_state(services, "laptop-a")["info"]["cameras"]) == 3)


def test_one_agent_away_out_of_two_makes_the_module_degraded(
    services: StudioServices,
    start_agent: StartAgent,
    transport: InProcessTransport,
    fake_clock: FakeClock,
) -> None:
    start_agent(host="10.0.0.5")
    start_agent(host="10.0.0.6")
    run(services, "add", host="10.0.0.5", id="laptop-a")
    run(services, "add", host="10.0.0.6", id="laptop-b")
    eventually(lambda: status_of(services, "laptop-b") == "ok")

    transport.unplug("10.0.0.6", 8890)
    advance_in_steps(fake_clock, 5 + DELIVERY_SLACK_SECONDS)

    eventually(lambda: services.supervisor.module_health("agents").state == HealthState.DEGRADED)
    assert "1 of 2 agents away" in services.supervisor.module_health("agents").message


def test_scan_finds_the_agent_and_add_from_the_result_persists_it(
    services: StudioServices, start_agent: StartAgent
) -> None:
    start_agent(host="192.168.50.23", agent_id="laptop-c")

    result = run(services, "scan", cidr="192.168.50.0/24")

    assert [(found["host"], found["agent_id"]) for found in result["found"]] == [
        ("192.168.50.23", "laptop-c")
    ]
    assert result["found"][0]["already_added"] is False
    # The page shows the results from the state, so every browser sees them.
    assert services.state.get("agents", "scan.found") == result["found"]

    run(services, "add", host="192.168.50.23", id="laptop-c")

    saved = services.settings.get("agents").model_dump()
    assert [agent["id"] for agent in saved["agents"]] == ["laptop-c"]
    assert run(services, "scan", cidr="192.168.50.0/24")["found"][0]["already_added"] is True


def test_a_scan_shares_one_http_client_across_every_address(
    services: StudioServices, transport: InProcessTransport
) -> None:
    # Making an HTTP client blocks for ~12 ms (it loads the trust store); one
    # per address would use up the 300 ms each probe gets. Regression test.
    before = transport.http_clients_made

    run(services, "scan", cidr="192.168.50.0/24")

    assert transport.http_clients_made - before == 1


def test_scanning_too_large_a_network_is_refused_with_a_reason(services: StudioServices) -> None:
    with pytest.raises(CommandError, match="at most 1024"):
        run(services, "scan", cidr="10.0.0.0/16")


def test_the_token_is_masked_in_the_api_and_a_wrong_one_shows_degraded(
    services: StudioServices, start_agent: StartAgent
) -> None:
    start_agent(token="right")
    run(services, "add", host=AGENT_HOST, id="laptop-a", token="wrong")
    eventually(lambda: status_of(services, "laptop-a") == "degraded")

    assert services.settings.masked_values("agents")["agents"][0]["token"] == SECRET_MASK

    # Fixing the token restarts the link with it.
    run(services, "update", id="laptop-a", token="right")
    eventually(lambda: status_of(services, "laptop-a") == "ok")


def test_removing_an_agent_drops_it_from_the_state(
    services: StudioServices, start_agent: StartAgent
) -> None:
    start_agent()
    run(services, "add", host=AGENT_HOST, id="laptop-a")
    eventually(lambda: status_of(services, "laptop-a") == "ok")

    run(services, "remove", id="laptop-a")

    assert services.state.get("agents", "agents") == {}
    assert services.supervisor.module_health("agents").message == "no agents enabled"


def test_a_disabled_agent_is_listed_but_not_linked(
    services: StudioServices, start_agent: StartAgent
) -> None:
    runner = start_agent()
    run(services, "add", host=AGENT_HOST, id="laptop-a")
    eventually(lambda: status_of(services, "laptop-a") == "ok")

    run(services, "update", id="laptop-a", enabled=False)

    assert status_of(services, "laptop-a") == "disabled"
    eventually(lambda: runner.run(lambda: len(runner.agent._subscribers)) == 0)


def test_adding_the_same_id_twice_is_refused(services: StudioServices) -> None:
    run(services, "add", host="10.0.0.5", id="laptop-a")

    with pytest.raises(CommandError, match="already configured"):
        run(services, "add", host="10.0.0.6", id="laptop-a")


def test_probe_reports_info_health_and_compatibility(
    services: StudioServices, start_agent: StartAgent
) -> None:
    start_agent(contract_version="2.0")

    result = run(services, "probe", host=AGENT_HOST)

    assert result["health"]["status"] == "ok"
    assert "speaks contract 2.0" in result["compatible_reason"]


def test_auto_paused_by_a_move_returns_to_auto_and_the_page_sees_it_live(
    services: StudioServices,
    start_agent: StartAgent,
    transport: InProcessTransport,
    fake_clock: FakeClock,
) -> None:
    # The acceptance scenario: put a camera in auto, move it from a plain
    # REST call (as the joystick would), and watch the mode on the page.
    start_agent()
    run(services, "add", host=AGENT_HOST, id="laptop-a")
    eventually(lambda: status_of(services, "laptop-a") == "ok")

    def camera_mode() -> Any:
        return agent_state(services, "laptop-a")["cameras"].get("cam1", {}).get("mode")

    async def put_in_auto_then_move() -> None:
        rest = AgentClient(f"http://{AGENT_HOST}:8890", "", fake_clock, transport)
        await rest.set_mode("cam1", Mode.AUTO, {"resume_after_ms": 2000})
        await rest.move("cam1", pan=0.3, ttl_ms=300)
        await rest.aclose()

    asyncio.run(put_in_auto_then_move())
    advance_in_steps(fake_clock, 1)
    eventually(lambda: camera_mode() == "auto_paused")

    # 0.3 s of motion plus 2 s of quiet, and auto is back without any input.
    advance_in_steps(fake_clock, 2)
    eventually(lambda: camera_mode() == "auto")
    events = [event["event"] for event in agent_state(services, "laptop-a")["events"]]
    assert "manual_override_ended" in events
