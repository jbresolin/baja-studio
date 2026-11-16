"""The node as one agent (contract §3 to §5): who it is, its health, its state pushes, its log.

The cameras (`cameras.py`) know their own state; this puts them together the
way the contract wants: `/agent/info`, `/agent/health`, the state message
every camera is in, the events they produced, and the command log. The push
worker (`node:push`, in `module.py`) calls `push()` four times a second, from
its own thread; the WebSockets live on the web server's event loop and are
reached through `StateHub.broadcast_from_thread`.
"""

import socket
from collections.abc import Callable
from typing import Any

from baja_studio import __version__
from baja_studio.agents.contract import (
    CONTRACT_VERSION,
    AgentHealth,
    AgentInfo,
    ErrorCode,
    EventMessage,
    LogEntry,
    StateMessage,
    Status,
)
from baja_studio.agents.server import AgentCommandLog, CameraCommandError, StateHub
from baja_studio.core.clock import Clock
from baja_studio.node.camera import NodeCamera
from baja_studio.node.settings import NodeSettings

# Four pushes a second: twice the contract's minimum (2 Hz), as the fake agent does.
PUSH_INTERVAL_SECONDS = 0.25
SOFTWARE_VERSION = f"baja-camera-node {__version__}"


def default_agent_id() -> str:
    """`node-<computer name>` in lower case: what a node is called until it is named."""
    return f"node-{socket.gethostname().lower() or 'laptop'}"


class NodeAgent:
    """The agent-level view of the node's cameras."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], NodeSettings],
        cameras: Callable[[], list[NodeCamera]],
    ) -> None:
        """`settings()` and `cameras()` are read on every call (they change without a restart)."""
        self._clock = clock
        self._settings = settings
        self._cameras = cameras
        self.hub = StateHub()
        self.log = AgentCommandLog(self.now_iso)
        self._started_at = clock.monotonic()

    def now_iso(self) -> str:
        """Now, in UTC, as the contract's timestamps are."""
        return self._clock.now_utc().isoformat()

    def token(self) -> str:
        """The token the studio must send (empty: none)."""
        return self._settings().token

    def camera(self, camera_id: str) -> NodeCamera:
        """A served camera, or `404 unknown_camera`."""
        for camera in self._cameras():
            if camera.id == camera_id:
                return camera
        served = ", ".join(camera.id for camera in self._cameras()) or "no cameras"
        raise CameraCommandError(
            ErrorCode.UNKNOWN_CAMERA, f"no camera {camera_id!r}; this node serves {served}"
        )

    def info(self) -> AgentInfo:
        """`GET /agent/info`: quick and without side effects (a subnet scan calls it)."""
        settings = self._settings()
        return AgentInfo(
            agent_id=settings.agent_id or default_agent_id(),
            name=settings.name or f"Camera node {socket.gethostname()}",
            contract_version=CONTRACT_VERSION,
            software_version=SOFTWARE_VERSION,
            cameras=[camera.info() for camera in self._cameras()],
            capabilities=["camera_node"],
        )

    def health(self) -> AgentHealth:
        """`GET /agent/health`: degraded while any camera is not ok, saying which and why."""
        statuses = {camera.id: camera.status() for camera in self._cameras()}
        troubled = [
            f"{camera_id}: {reason or status.value}"
            for camera_id, (status, reason) in statuses.items()
            if status != Status.OK
        ]
        return AgentHealth(
            status=Status.DEGRADED if troubled else Status.OK,
            detail="; ".join(troubled),
            uptime_s=round(self._clock.monotonic() - self._started_at, 3),
            cameras={camera_id: status for camera_id, (status, _) in statuses.items()},
        )

    def log_tail(self, limit: int) -> list[LogEntry]:
        """`GET /agent/log`."""
        return self.log.tail(limit)

    def state_message(self) -> dict[str, Any]:
        """Every camera's state, as pushed."""
        message = StateMessage(
            ts=self.now_iso(), cameras={camera.id: camera.state() for camera in self._cameras()}
        )
        return message.model_dump(mode="json")

    def greeting(self) -> list[dict[str, Any]]:
        """What a newly accepted WebSocket gets at once: the state (nothing to restore here)."""
        return [self.state_message()]

    def push(self) -> None:
        """One push cycle: device events, any pending events, then the state."""
        for camera in self._cameras():
            camera.check_status()
        self.send_pending_events()
        self.hub.broadcast_from_thread(self.state_message())

    def send_pending_events(self) -> None:
        """Send the events the cameras produced since the last time."""
        timestamp = self.now_iso()
        for camera in self._cameras():
            for event, detail in camera.take_events():
                message = EventMessage(
                    ts=timestamp, camera_id=camera.id, event=event, detail=detail
                )
                self.hub.broadcast_from_thread(message.model_dump(mode="json"))
