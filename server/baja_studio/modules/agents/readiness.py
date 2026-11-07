"""The agents module's readiness checks: each agent reachable, compatible, our token accepted.

The live link to every agent (`agents/registry.py`) already knows all three,
so a check reads its published view and sends nothing to the agent.
"""

from functools import partial
from typing import Any

from pydantic import ValidationError

from baja_studio.agents.client import incompatibility
from baja_studio.agents.contract import AgentInfo
from baja_studio.agents.registry import AgentEntry
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed

AGENTS_LINK = "/modules/agents"
TOKEN_FIX = (
    "Copy the token from the agent's own page into this agent's entry on the Agents page "
    "(Edit → token), or clear it on both sides."
)
CONTRACT_FIX = (
    "Update the camera-laptop agent (or the studio) so both speak the same MAJOR "
    "contract version (docs/agent-api.md)."
)


def agent_checks(entries: list[AgentEntry], agents_state: dict[str, Any]) -> list[ReadinessCheck]:
    """One check per enabled agent; `agents_state` is `state.agents.agents`."""
    checks = []
    for entry in entries:
        if not entry.enabled:
            continue
        view = agents_state.get(entry.id) or {}
        checks.append(
            ReadinessCheck(
                f"agent:{entry.id}",
                f"Agent {entry.id}",
                partial(_agent, entry, view),
                fix=(
                    f"Check the laptop is on and on the studio network, and its agent answers "
                    f"at {entry.base_url} (Agents page → Probe)."
                ),
                link=AGENTS_LINK,
            )
        )
    return checks


def _agent(entry: AgentEntry, view: dict[str, Any]) -> CheckOutcome:
    info = view.get("info")
    if isinstance(info, dict):
        try:
            problem = incompatibility(AgentInfo.model_validate(info))
        except ValidationError:
            problem = "the agent's /agent/info does not follow the contract"
        if problem:
            return failed(problem, CONTRACT_FIX)
    status, reason = view.get("status") or "offline", view.get("reason") or ""
    if status == "ok":
        version = info.get("contract_version") if isinstance(info, dict) else "?"
        cameras = len(view.get("cameras") or {})
        return passed(f"linked, contract {version}, {cameras} camera{'s' if cameras != 1 else ''}")
    detail = f"{status}: {reason}" if reason else status
    if "token" in reason:
        return failed(detail, TOKEN_FIX)
    return failed(detail)
