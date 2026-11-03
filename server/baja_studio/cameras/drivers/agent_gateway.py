"""How agent-owned camera drivers reach the agents module (which implements this)."""

from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Protocol

from baja_studio.agents.client import AgentClient

AgentCall = Callable[[AgentClient], Awaitable[Any]]


@dataclass(frozen=True)
class AgentLinkView:
    """What the agents module knows about one agent right now."""

    status: str  # connecting, ok, degraded, offline, disabled
    reason: str
    cameras: dict[str, dict[str, Any]]  # CameraState as JSON, by the agent's camera id
    info: dict[str, Any] | None  # AgentInfo as JSON


class AgentGateway(Protocol):
    """The agents module, as camera drivers see it. Safe to call from any thread."""

    def link(self, agent_id: str) -> AgentLinkView | None:
        """The agent's link, or None if no such agent is configured."""

    def submit(self, agent_id: str, call: AgentCall) -> "Future[Any]":
        """Run `call(client)` on the agents' event loop; the future holds its result."""

    def direct_fallback_after_s(self) -> float:
        """Seconds offline before direct fallback (the agents module's setting)."""
