"""The agents module: which camera-laptop agents the studio talks to, and how they are.

Settings hold the list of agents (edited on the Agents page with the `add`,
`update` and `remove` commands), the network the Scan button searches, and
the direct-fallback delay that camera control (WP2) will use.

Readiness checks (`readiness.py`): each enabled agent linked, compatible,
our token accepted.

One worker runs an event loop with an `AgentRegistry` on it: a live link to
every enabled agent, published to `state.agents.agents`. Its health is the
summary of all agents, so the home page card says when one is away.

The module is also the cameras module's `AgentGateway`: agent-owned camera
drivers read link state through `link` and send commands through `submit`.
"""

import asyncio
import socket
import threading
from concurrent.futures import Future
from typing import Any

import httpx2
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from baja_studio.agents.client import AgentClient, incompatibility
from baja_studio.agents.contract import AGENT_PORT, STUDIO_ID_HEADER
from baja_studio.agents.registry import AgentEntry, AgentRegistry
from baja_studio.agents.scan import (
    PROBE_TIMEOUT_SECONDS,
    FoundAgent,
    ScanRangeError,
    addresses_to_scan,
    http_info_probe,
    scan_subnet,
)
from baja_studio.agents.transport import AgentTransport, NetworkTransport
from baja_studio.cameras.drivers.agent_gateway import AgentCall, AgentLinkView
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.settings import ModuleSettings
from baja_studio.core.supervisor import WorkerContext, WorkerSpec
from baja_studio.modules.agents.readiness import agent_checks

LINKS_WORKER = "links"
SCAN_TIMEOUT = httpx2.Timeout(PROBE_TIMEOUT_SECONDS)


class AgentsSettings(ModuleSettings):
    """Settings of the agents module."""

    agents: list[AgentEntry] = Field(
        default_factory=list,
        description="Edited on the Agents page.",
        json_schema_extra={"managed_elsewhere": True},
    )
    scan_cidr: str = Field(
        default="",
        description="Network the Scan button searches, e.g. 192.168.1.0/24 (a /22 at most).",
    )
    direct_fallback_after_s: float = Field(
        default=10.0,
        ge=1.0,
        le=600.0,
        description=(
            "Seconds an agent must be offline before the studio drives its cameras directly "
            "(only cameras with a direct fallback configured; camera control, WP2)."
        ),
    )

    @field_validator("scan_cidr")
    @classmethod
    def _scannable(cls, cidr: str) -> str:
        if cidr.strip():
            addresses_to_scan(cidr)  # raises ScanRangeError (a ValueError) with the reason
        return cidr.strip()

    @model_validator(mode="after")
    def _distinct_agents(self) -> "AgentsSettings":
        ids = [entry.id for entry in self.agents]
        addresses = [(entry.host, entry.port) for entry in self.agents]
        if len(set(ids)) != len(ids):
            raise ValueError("two agents have the same id")
        if len(set(addresses)) != len(addresses):
            raise ValueError("two agents have the same host and port")
        return self


def default_studio_id() -> str:
    """What the studio calls itself in `X-Studio-Id`, so agent logs say who moved a camera."""
    return f"baja-studio@{socket.gethostname()}"


class AgentsModule(Module):
    """Agent registry, live status, subnet scan."""

    name = "agents"
    title = "Camera agents"
    SettingsModel = AgentsSettings

    def __init__(self, transport: AgentTransport | None = None, studio_id: str = "") -> None:
        """`transport` is the network unless a test passes the in-process one."""
        super().__init__()
        self._transport = transport or NetworkTransport()
        self._studio_id = studio_id or default_studio_id()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._registry: AgentRegistry | None = None
        self._subscribed = False

    def initial_state(self) -> dict[str, Any]:
        """No agents known, no scan run."""
        return {"agents": {}, "scan": {"running": False, "cidr": "", "found": [], "error": ""}}

    def start(self, context: ModuleContext) -> None:
        """Follow settings so added or edited agents are linked at once."""
        super().start(context)
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._apply_new_settings)

    def workers(self) -> list[WorkerSpec]:
        """One worker: the event loop holding every agent link."""
        return [WorkerSpec(LINKS_WORKER, self._run_links)]

    def readiness_checks(self) -> list[ReadinessCheck]:
        """Each enabled agent, from its live link (see `readiness.py`)."""
        agents_state = self.context.state.get(self.name, "agents") or {}
        return agent_checks(self._settings().agents, agents_state)

    # --- the links worker ----------------------------------------------------------

    def _run_links(self, worker: WorkerContext) -> None:
        asyncio.run(self._links_main(worker))

    async def _links_main(self, worker: WorkerContext) -> None:
        registry = AgentRegistry(
            self.context.clock,
            self._transport,
            self.context.update_state,
            worker.report_health,
            self._studio_id,
            self.context.logger,
        )
        with self._lock:
            self._loop, self._registry = asyncio.get_running_loop(), registry
        try:
            registry.apply(self._settings().agents)
            await asyncio.to_thread(worker.stop_requested.wait)
        finally:
            with self._lock:
                self._loop, self._registry = None, None
            await registry.close()

    def _apply_new_settings(self, values: BaseModel) -> None:
        """Settings subscriber: hand the new agent list to the registry's loop."""
        assert isinstance(values, AgentsSettings)
        with self._lock:
            loop, registry = self._loop, self._registry
        if loop is not None and registry is not None:
            loop.call_soon_threadsafe(registry.apply, values.agents)

    def _settings(self) -> AgentsSettings:
        settings = self.context.current_settings()
        assert isinstance(settings, AgentsSettings)
        return settings

    # --- the cameras module's gateway ---------------------------------------------------

    def link(self, agent_id: str) -> AgentLinkView | None:
        """The agent's link as published (None if not configured or the module is off)."""
        if self._context is None:
            return None
        view = self.context.state.get(self.name, f"agents.{agent_id}")
        if not isinstance(view, dict):
            return None
        return AgentLinkView(
            status=str(view.get("status", "")),
            reason=str(view.get("reason", "")),
            cameras=dict(view.get("cameras") or {}),
            info=view.get("info"),
        )

    def submit(self, agent_id: str, call: AgentCall) -> "Future[Any]":
        """Run `call(client)` on the links' event loop; fails if the agent is not linked."""
        with self._lock:
            loop, registry = self._loop, self._registry
        if loop is None or registry is None:
            failed: Future[Any] = Future()
            failed.set_exception(RuntimeError("the agents module is not running"))
            return failed

        async def on_the_loop() -> Any:
            client = registry.client(agent_id)
            if client is None:
                raise RuntimeError(f"agent {agent_id!r} is not configured or is disabled")
            return await call(client)

        return asyncio.run_coroutine_threadsafe(on_the_loop(), loop)

    def direct_fallback_after_s(self) -> float:
        """The fallback delay from this module's settings."""
        if self._context is None:
            return AgentsSettings().direct_fallback_after_s
        return self._settings().direct_fallback_after_s

    # --- commands --------------------------------------------------------------------

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`scan {cidr?}`, `add {host, port?, id?, token?}`, `update {id, ...}`,
        `remove {id}`, `probe {id}` or `probe {host, port?, token?}`."""
        if command == "scan":
            return self._scan(str(args.get("cidr") or self._settings().scan_cidr))
        if command == "add":
            return self._add(args)
        if command == "update":
            return self._update(args)
        if command == "remove":
            entry = self._entry(args)
            self._save([other for other in self._settings().agents if other.id != entry.id])
            return {"removed": entry.id}
        if command == "probe":
            return self._probe(args)
        raise CommandError(
            f"agents has no command {command!r}; try scan, add, update, remove or probe"
        )

    def _scan(self, cidr: str) -> dict[str, Any]:
        if not cidr:
            raise CommandError("say which network to scan, e.g. 192.168.1.0/24")
        try:
            addresses_to_scan(cidr)
        except ScanRangeError as refused:
            raise CommandError(str(refused)) from None
        self.context.update_state("scan", {"running": True, "cidr": cidr, "found": [], "error": ""})
        try:
            found = asyncio.run(self._scan_with_one_client(cidr))
        except Exception as error:
            self.context.update_state(
                "scan", {"running": False, "cidr": cidr, "found": [], "error": str(error)}
            )
            raise
        configured = {(entry.host, entry.port) for entry in self._settings().agents}
        results = [
            {
                "host": agent.host,
                "port": AGENT_PORT,
                "agent_id": agent.info.agent_id,
                "name": agent.info.name,
                "contract_version": agent.info.contract_version,
                "cameras": [camera.id for camera in agent.info.cameras],
                "already_added": (agent.host, AGENT_PORT) in configured,
            }
            for agent in found
        ]
        scan_state = {"running": False, "cidr": cidr, "found": results, "error": ""}
        self.context.update_state("scan", scan_state)
        return scan_state

    async def _scan_with_one_client(self, cidr: str) -> list[FoundAgent]:
        headers = {STUDIO_ID_HEADER: self._studio_id}
        # The base URL is unused: every probe names its own host.
        async with self._transport.http_client("http://scan", headers, SCAN_TIMEOUT) as http:
            return await scan_subnet(cidr, http_info_probe(http))

    def _add(self, args: dict[str, Any]) -> dict[str, Any]:
        host = str(args.get("host") or "").strip()
        if not host:
            raise CommandError("add needs the agent's 'host' (its IP address or name)")
        agent_id = str(args.get("id") or host.replace(".", "-"))
        current = self._settings().agents
        if any(entry.id == agent_id for entry in current):
            raise CommandError(f"an agent called {agent_id!r} is already configured")
        entry = self._validated_entry(
            {
                "id": agent_id,
                "host": host,
                "port": args.get("port", AGENT_PORT),
                "token": args.get("token", ""),
            }
        )
        self._save([*current, entry])
        return {"added": entry.id}

    def _update(self, args: dict[str, Any]) -> dict[str, Any]:
        entry = self._entry(args)
        changes: dict[str, Any] = {
            key: args[key] for key in ("host", "port", "token", "enabled") if key in args
        }
        updated = self._validated_entry({**entry.model_dump(), **changes})
        agents = self._settings().agents
        self._save([updated if other.id == entry.id else other for other in agents])
        return {"updated": entry.id}

    def _probe(self, args: dict[str, Any]) -> dict[str, Any]:
        if "id" in args:
            entry = self._entry(args)
        else:
            entry = self._validated_entry(
                {
                    "id": "probe",
                    "host": str(args.get("host") or ""),
                    "port": args.get("port", AGENT_PORT),
                    "token": args.get("token", ""),
                }
            )

        async def probe() -> dict[str, Any]:
            client = AgentClient(
                entry.base_url, entry.token, self.context.clock, self._transport, self._studio_id
            )
            try:
                info = await client.info()
                health = await client.health()
            finally:
                await client.aclose()
            return {
                "info": info.model_dump(mode="json"),
                "health": health.model_dump(mode="json"),
                "compatible_reason": incompatibility(info) or "",
            }

        try:
            return asyncio.run(probe())
        except Exception as error:
            raise CommandError(f"could not probe {entry.base_url}: {error}") from None

    def _entry(self, args: dict[str, Any]) -> AgentEntry:
        agent_id = str(args.get("id") or "")
        for entry in self._settings().agents:
            if entry.id == agent_id:
                return entry
        raise CommandError(f"no agent called {agent_id!r} is configured")

    def _validated_entry(self, values: dict[str, Any]) -> AgentEntry:
        try:
            return AgentEntry.model_validate(values)
        except ValidationError as invalid:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
                for problem in invalid.errors()
            )
            raise CommandError(f"invalid agent: {problems}") from None

    def _save(self, entries: list[AgentEntry]) -> None:
        try:
            self.context.settings.update(
                self.name, {"agents": [entry.model_dump() for entry in entries]}
            )
        except ValidationError as invalid:
            raise CommandError(str(invalid.errors()[0]["msg"])) from None
