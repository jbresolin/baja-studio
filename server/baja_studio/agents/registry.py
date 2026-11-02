"""Keeps a live link to every configured agent and publishes what they report.

Lives on one event loop (the agents module runs it in a worker thread). For
each enabled agent it runs `AgentClient.subscribe` and turns the updates into
the module's state, `state.agents.agents.<id>`:

    {host, port, enabled, status, reason, info, cameras: {id: CameraState},
     events: [last few EventMessages], last_seen}

and into one summary health for the home page ("1 of 2 agents offline").
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from baja_studio.agents.client import AgentClient
from baja_studio.agents.contract import AGENT_PORT, EventName
from baja_studio.agents.subscription import (
    AgentUpdate,
    EventUpdate,
    LinkStatus,
    StateUpdate,
    StatusUpdate,
)
from baja_studio.agents.transport import AgentTransport
from baja_studio.core.clock import Clock
from baja_studio.core.supervisor import HealthState

RECENT_EVENTS_KEPT = 10
LAST_SEEN_PUBLISH_INTERVAL_SECONDS = 1.0
# If `subscribe` itself crashes (a bug, not a network problem), try again after this.
LINK_CRASH_RETRY_SECONDS = 5.0
DISABLED = "disabled"

StatePublisher = Callable[[str, Any], None]
HealthReporter = Callable[[HealthState, str], None]


class AgentEntry(BaseModel):
    """One configured agent, as stored in the agents module's settings."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$",
        description="Short name used in the camera registry, e.g. laptop-a.",
    )
    host: str = Field(min_length=1, max_length=253, description="IP address or host name.")
    port: int = Field(default=AGENT_PORT, ge=1, le=65535)
    token: str = Field(default="", json_schema_extra={"secret": True})
    enabled: bool = True

    @property
    def base_url(self) -> str:
        """Where the agent's HTTP API is, e.g. http://10.0.0.5:8890."""
        return f"http://{self.host}:{self.port}"


class _Link:
    """The studio's link to one enabled agent."""

    def __init__(self, entry: AgentEntry, client: AgentClient) -> None:
        self.entry = entry
        self.client = client
        self.task: asyncio.Task[None] | None = None
        self.status: str = LinkStatus.CONNECTING
        self.reason = ""
        self.info: dict[str, Any] | None = None
        self.cameras: dict[str, Any] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS_KEPT)
        self.last_seen: str | None = None
        self.last_seen_published_at = float("-inf")


class AgentRegistry:
    """Every agent link; all methods must be called on the registry's event loop."""

    def __init__(
        self,
        clock: Clock,
        transport: AgentTransport,
        publish: StatePublisher,
        report_health: HealthReporter,
        studio_id: str,
        logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """`publish(path, value)` writes the module state; `report_health` its health."""
        self._clock = clock
        self._transport = transport
        self._publish = publish
        self._report_health = report_health
        self._studio_id = studio_id
        self._logger = logger
        self._entries: list[AgentEntry] = []
        self._links: dict[str, _Link] = {}
        # Short jobs (closing a client, re-reading info); kept so they are not lost mid-run.
        self._background: set[asyncio.Task[None]] = set()

    def apply(self, entries: list[AgentEntry]) -> None:
        """Make the links match the configured agents: start, stop or restart as needed.

        Called at start and after every settings save. A link whose host,
        port or token changed is restarted; an unchanged one is left alone.
        """
        self._entries = list(entries)
        wanted = {entry.id: entry for entry in entries if entry.enabled}
        for agent_id in list(self._links):
            if self._links[agent_id].entry != wanted.get(agent_id):
                self._stop_link(agent_id)
        for agent_id, entry in wanted.items():
            if agent_id not in self._links:
                self._start_link(entry)
        self._publish("agents", {entry.id: self._view(entry) for entry in self._entries})
        self._summarise_health()

    def client(self, agent_id: str) -> AgentClient | None:
        """The client for an enabled agent (used by camera drivers from WP2 on)."""
        link = self._links.get(agent_id)
        return link.client if link else None

    async def close(self) -> None:
        """Stop every link and close its HTTP connections; called when the worker stops."""
        for agent_id in list(self._links):
            self._stop_link(agent_id)
        # Only our own tasks: the loop may be shared (it is, in tests).
        await asyncio.gather(*list(self._background), return_exceptions=True)

    # --- links ---------------------------------------------------------------------

    def _start_link(self, entry: AgentEntry) -> None:
        client = AgentClient(
            entry.base_url, entry.token, self._clock, self._transport, self._studio_id
        )
        link = _Link(entry, client)
        self._links[entry.id] = link
        link.task = asyncio.create_task(self._run_link(link), name=f"agent-link-{entry.id}")

    def _stop_link(self, agent_id: str) -> None:
        link = self._links.pop(agent_id)
        if link.task is not None:
            link.task.cancel()
            # Kept until it has finished unwinding, so `close` can wait for it.
            self._background.add(link.task)
            link.task.add_done_callback(self._background.discard)
        self._in_background(link.client.aclose())

    async def _run_link(self, link: _Link) -> None:
        while True:
            try:
                await link.client.subscribe(lambda update: self._on_update(link, update))
            except Exception:
                self._logger.exception("link to agent %s failed; retrying", link.entry.id)
                self._set_status(link, LinkStatus.DEGRADED, "internal error; see the log")
                await self._clock.sleep(LINK_CRASH_RETRY_SECONDS)

    # --- updates from the agent -----------------------------------------------------

    def _on_update(self, link: _Link, update: AgentUpdate) -> None:
        if self._links.get(link.entry.id) is not link:
            return  # a late update from a link that has just been stopped
        if isinstance(update, StatusUpdate):
            if update.info is not None:
                link.info = update.info.model_dump(mode="json")
            self._set_status(link, update.status, update.reason)
            return
        self._mark_seen(link)
        if isinstance(update, StateUpdate):
            cameras = {
                camera_id: state.model_dump(mode="json")
                for camera_id, state in update.message.cameras.items()
            }
            if cameras != link.cameras:
                link.cameras = cameras
                self._publish(f"agents.{link.entry.id}.cameras", cameras)
        elif isinstance(update, EventUpdate):
            event = update.message.model_dump(mode="json")
            link.events.appendleft(event)
            self._publish(f"agents.{link.entry.id}.events", list(link.events))
            self._logger.info(
                "agent %s: %s %s %s",
                link.entry.id,
                event["camera_id"] or "",
                event["event"],
                event["detail"],
            )
            if update.message.event == EventName.PRESETS_CHANGED:
                self._in_background(self._refresh_info(link))

    def _in_background(self, job: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(job)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _refresh_info(self, link: _Link) -> None:
        """Presets live in `/agent/info`, so read it again when they change."""
        with contextlib.suppress(Exception):
            link.info = (await link.client.info()).model_dump(mode="json")
            if self._links.get(link.entry.id) is link:
                self._publish(f"agents.{link.entry.id}.info", link.info)

    def _set_status(self, link: _Link, status: str, reason: str) -> None:
        if status == LinkStatus.OK:
            self._mark_seen(link, force=True)
        if (status, reason) != (link.status, link.reason):
            log = self._logger.info if status == LinkStatus.OK else self._logger.warning
            log("agent %s is %s%s", link.entry.id, status, f": {reason}" if reason else "")
        link.status, link.reason = status, reason
        self._publish(f"agents.{link.entry.id}", self._view(link.entry))
        self._summarise_health()

    def _mark_seen(self, link: _Link, force: bool = False) -> None:
        """Record when the agent was last heard from; published at most once a second."""
        link.last_seen = self._clock.now_utc().isoformat()
        now = self._clock.monotonic()
        if force or now - link.last_seen_published_at >= LAST_SEEN_PUBLISH_INTERVAL_SECONDS:
            link.last_seen_published_at = now
            self._publish(f"agents.{link.entry.id}.last_seen", link.last_seen)

    # --- state and health ----------------------------------------------------------

    def _view(self, entry: AgentEntry) -> dict[str, Any]:
        """What the page shows for one configured agent (never its token)."""
        link = self._links.get(entry.id)
        return {
            "host": entry.host,
            "port": entry.port,
            "enabled": entry.enabled,
            "status": link.status if link else DISABLED,
            "reason": link.reason if link else "",
            "info": link.info if link else None,
            "cameras": link.cameras if link else {},
            "events": list(link.events) if link else [],
            "last_seen": link.last_seen if link else None,
        }

    def _summarise_health(self) -> None:
        """One health for the module: ok, degraded (some agents away) or offline (all)."""
        links = list(self._links.values())
        if not links:
            self._report_health(HealthState.OK, "no agents enabled")
            return
        away = [link for link in links if link.status in (LinkStatus.OFFLINE, LinkStatus.DEGRADED)]
        connecting = [link for link in links if link.status == LinkStatus.CONNECTING]
        if not away:
            message = f"connecting to {len(connecting)}" if connecting else ""
            ok_count = len(links) - len(connecting)
            summary = f"{ok_count} of {len(links)} agents ok"
            self._report_health(HealthState.OK, f"{summary}; {message}" if message else summary)
            return
        details = ", ".join(f"{link.entry.id} {link.status}" for link in away)
        if len(away) == len(links):
            self._report_health(HealthState.OFFLINE, f"all agents away: {details}")
        else:
            self._report_health(
                HealthState.DEGRADED, f"{len(away)} of {len(links)} agents away: {details}"
            )
