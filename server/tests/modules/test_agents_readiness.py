"""Tests for the agents module's readiness checks (modules/agents/readiness.py).

The agents module runs with fake camera-laptop agents on the in-process
network. A check reads the module's live link to each agent: reachable,
same contract MAJOR version, our token accepted.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.readiness import CheckStatus
from baja_studio.core.services import StudioServices
from baja_studio.fakes.in_process import InProcessTransport
from baja_studio.modules.agents import AgentsModule
from baja_studio.modules.agents.readiness import CONTRACT_FIX, TOKEN_FIX
from tests.conftest import AppFactory, FakeClock, eventually
from tests.fake_agents import AGENT_HOST, StartAgent
from tests.modules.readiness_runner import assert_fix_given, run_module_checks


@pytest.fixture
def services(
    make_app: AppFactory, transport: InProcessTransport, start_agent: StartAgent
) -> Iterator[StudioServices]:
    """Just the agents module (stopped before the fake agents, as in test_agents.py)."""
    app = make_app(lambda registry: [AgentsModule(transport, studio_id="test-studio")])
    with TestClient(app):
        yield app.state.services


def add(services: StudioServices, **args: Any) -> None:
    services.registry.dispatch_command("agents", "add", {"host": AGENT_HOST, **args}, "web:t")


def status_of(services: StudioServices) -> Any:
    return services.state.get("agents", "agents.laptop-a.status")


def test_a_linked_agent_passes_and_says_what_it_has(
    services: StudioServices, start_agent: StartAgent, fake_clock: FakeClock
) -> None:
    start_agent(ptz_cameras=2)
    add(services, id="laptop-a")
    eventually(lambda: status_of(services) == "ok")

    result = run_module_checks(services, "agents", fake_clock)["agent:laptop-a"]

    assert result.status == CheckStatus.OK
    assert result.detail.startswith("linked, contract 1.")
    assert result.detail.endswith("2 cameras")


def test_an_agent_that_never_answered_fails(
    services: StudioServices, fake_clock: FakeClock
) -> None:
    add(services, id="laptop-a")  # nothing at that address: the link keeps trying
    eventually(lambda: status_of(services) is not None)

    result = run_module_checks(services, "agents", fake_clock)["agent:laptop-a"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.split(":")[0] in ("connecting", "offline")
    assert f"http://{AGENT_HOST}:8890" in result.fix


def test_a_refused_token_fails_with_the_token_fix(
    services: StudioServices, start_agent: StartAgent, fake_clock: FakeClock
) -> None:
    start_agent(token="right")
    add(services, id="laptop-a", token="wrong")
    eventually(lambda: status_of(services) == "degraded")

    result = run_module_checks(services, "agents", fake_clock)["agent:laptop-a"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == "degraded: the agent refused our token"
    assert result.fix == TOKEN_FIX


def test_an_agent_with_another_contract_major_fails_with_the_contract_fix(
    services: StudioServices, start_agent: StartAgent, fake_clock: FakeClock
) -> None:
    start_agent(contract_version="9.0")
    add(services, id="laptop-a")
    eventually(lambda: status_of(services) == "degraded")

    result = run_module_checks(services, "agents", fake_clock)["agent:laptop-a"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.startswith("agent speaks contract 9.0")
    assert result.fix == CONTRACT_FIX
    assert_fix_given(result)


def test_a_disabled_agent_is_not_checked(
    services: StudioServices, start_agent: StartAgent, fake_clock: FakeClock
) -> None:
    start_agent()
    add(services, id="laptop-a")
    services.registry.dispatch_command(
        "agents", "update", {"id": "laptop-a", "enabled": False}, "web:t"
    )

    assert run_module_checks(services, "agents", fake_clock) == {}
