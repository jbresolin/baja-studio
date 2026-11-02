"""Tests for the set of live agent links (agents/registry.py).

The registry is driven directly here, on the test's own event loop: its
state updates go into a dict and its health reports into a list, so we can
see exactly what it publishes. Fake agents run on the fake clock.
"""

import logging
from typing import Any

import pytest

from baja_studio.agents.registry import AgentEntry, AgentRegistry
from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.in_process import InProcessTransport
from tests.conftest import FakeClock, eventually_async
from tests.fake_agents import AGENT_HOST, StartAgent

pytestmark = pytest.mark.anyio


class Published:
    """What the registry has published: state paths and health reports."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.health: list[tuple[HealthState, str]] = []

    def publish(self, path: str, value: Any) -> None:
        self.state[path] = value

    def report(self, state: HealthState, message: str) -> None:
        self.health.append((state, message))

    def status_of(self, agent_id: str) -> Any:
        whole = self.state.get(f"agents.{agent_id}") or self.state.get("agents", {}).get(agent_id)
        return whole and whole["status"]


@pytest.fixture
def published() -> Published:
    return Published()


@pytest.fixture
def registry(
    fake_clock: FakeClock, transport: InProcessTransport, published: Published
) -> AgentRegistry:
    return AgentRegistry(
        fake_clock,
        transport,
        published.publish,
        published.report,
        "test-studio",
        logging.getLogger("test"),
    )


def laptop_a(**changes: Any) -> AgentEntry:
    return AgentEntry.model_validate({"id": "laptop-a", "host": AGENT_HOST, **changes})


async def test_an_unchanged_agent_keeps_its_link_when_settings_are_saved_again(
    start_agent: StartAgent, registry: AgentRegistry, published: Published
) -> None:
    start_agent()
    registry.apply([laptop_a()])
    await eventually_async(lambda: published.status_of("laptop-a") == "ok")
    client = registry.client("laptop-a")

    # Another setting changed; laptop-a's entry did not. No reconnect wanted.
    registry.apply([laptop_a()])

    assert registry.client("laptop-a") is client
    await registry.close()


async def test_a_changed_token_restarts_the_link_with_a_new_client(
    start_agent: StartAgent, registry: AgentRegistry, published: Published
) -> None:
    start_agent()
    registry.apply([laptop_a()])
    client = registry.client("laptop-a")

    registry.apply([laptop_a(token="new")])

    assert registry.client("laptop-a") is not client
    await registry.close()


async def test_a_disabled_agent_is_published_without_a_link(
    registry: AgentRegistry, published: Published
) -> None:
    registry.apply([laptop_a(enabled=False)])

    assert registry.client("laptop-a") is None
    assert published.state["agents"]["laptop-a"]["status"] == "disabled"
    # Disabled agents do not count towards health.
    assert published.health[-1] == (HealthState.OK, "no agents enabled")
    await registry.close()


async def test_the_published_view_never_contains_the_token(
    registry: AgentRegistry, published: Published
) -> None:
    registry.apply([laptop_a(token="very-secret")])

    assert "very-secret" not in repr(published.state)
    await registry.close()
