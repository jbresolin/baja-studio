"""A complete fake camera-laptop agent, speaking contract 1.0 (docs/agent-api.md).

Run it on the bench, with no laptop or camera:

    python -m baja_studio.fakes.fake_agent --port 8890 --cameras 2

Every camera is a `SimulatedCamera`. Failure switches under `/_fake/...`
(see fake_agent_routes.py) make it go silent, drop its WebSockets, answer
slowly, lose a device or a tracker, so the studio's recovery paths can be
exercised. Tests use the same app in-process (fakes/in_process.py).
"""

import argparse
import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI

from baja_studio.agents.contract import (
    AGENT_PORT,
    CONTRACT_VERSION,
    AgentHealth,
    AgentInfo,
    CameraKind,
    ErrorCode,
    EventMessage,
    LogEntry,
    StateMessage,
    Status,
)
from baja_studio.core.clock import Clock, SystemClock
from baja_studio.fakes.fake_agent_routes import add_fake_agent_routes
from baja_studio.fakes.simulated_ptz import CameraCommandError, Moment, SimulatedCamera

logger = logging.getLogger(__name__)

# Four pushes a second: twice the contract's minimum, so a slow test machine
# still comfortably meets it.
PUSH_INTERVAL_SECONDS = 0.25
LOG_LENGTH = 500
SOFTWARE_VERSION = "fake-agent 1.0"

# What a subscriber's queue carries: a message to send, or None for "close now".
SubscriberQueue = asyncio.Queue[dict[str, Any] | None]


class FakeAgent:
    """The fake agent's state: cameras, subscribers, command log and failure switches."""

    def __init__(
        self,
        clock: Clock,
        ptz_cameras: int = 2,
        crop_cameras: int = 0,
        token: str = "",
        agent_id: str = "fake-agent",
        name: str = "Fake agent",
        contract_version: str = CONTRACT_VERSION,
    ) -> None:
        """Cameras are called cam1, cam2, ... (PTZ) and crop1, crop2, ... (crop).

        `contract_version` can be set to something else to play an agent the
        studio must refuse.
        """
        self.clock = clock
        self.token = token
        self.agent_id = agent_id
        self.name = name
        self.contract_version = contract_version
        self.cameras: dict[str, SimulatedCamera] = {}
        for number in range(1, ptz_cameras + 1):
            camera = SimulatedCamera(f"cam{number}", f"Fake PTZ {number}", CameraKind.PTZ)
            self.cameras[camera.id] = camera
        for number in range(1, crop_cameras + 1):
            camera = SimulatedCamera(f"crop{number}", f"Fake crop {number}", CameraKind.CROP)
            self.cameras[camera.id] = camera
        # Failure switches.
        self.silent = False
        self.delay_seconds = 0.0
        self._subscribers: set[SubscriberQueue] = set()
        self._log: deque[LogEntry] = deque(maxlen=LOG_LENGTH)
        self._started_at = clock.monotonic()
        self._push_task: asyncio.Task[None] | None = None

    def now(self) -> Moment:
        """The current time in both forms the cameras need."""
        return Moment(self.clock.monotonic(), self.clock.now_utc().isoformat())

    async def start(self) -> None:
        """Start pushing state; called when the app starts serving."""
        self._started_at = self.clock.monotonic()
        self._push_task = asyncio.create_task(self._push_state_forever())

    async def stop(self) -> None:
        """Stop pushing and close every WebSocket; called at app shutdown."""
        if self._push_task is not None:
            self._push_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._push_task
        self.drop_websockets()

    # --- what the routes call --------------------------------------------------------

    def camera(self, camera_id: str) -> SimulatedCamera:
        """The camera called `camera_id`, or a 404 `unknown_camera` error."""
        camera = self.cameras.get(camera_id)
        if camera is None:
            raise CameraCommandError(
                ErrorCode.UNKNOWN_CAMERA,
                f"no camera {camera_id!r}; this agent has {', '.join(self.cameras)}",
            )
        return camera

    def info(self) -> AgentInfo:
        """`GET /agent/info`."""
        return AgentInfo(
            agent_id=self.agent_id,
            name=self.name,
            contract_version=self.contract_version,
            software_version=SOFTWARE_VERSION,
            cameras=[camera.info() for camera in self.cameras.values()],
            capabilities=["fake_controls"],
        )

    def health(self) -> AgentHealth:
        """`GET /agent/health`: degraded while any camera's device is offline."""
        statuses = {
            camera_id: Status.OK if camera.online else Status.OFFLINE
            for camera_id, camera in self.cameras.items()
        }
        offline = [camera_id for camera_id, status in statuses.items() if status != Status.OK]
        return AgentHealth(
            status=Status.DEGRADED if offline else Status.OK,
            detail=f"offline: {', '.join(offline)}" if offline else "",
            uptime_s=round(self.clock.monotonic() - self._started_at, 3),
            cameras=statuses,
        )

    def log_tail(self, limit: int) -> list[LogEntry]:
        """The newest `limit` command log entries, newest first."""
        return list(reversed(self._log))[:limit]

    def record_command(
        self, camera_id: str, source: str, command: str, args: dict[str, Any], result: str
    ) -> None:
        """Add one entry to the agent's own command log."""
        entry = LogEntry(
            ts=self.now().iso,
            module=camera_id,
            source=source,
            command=command,
            args=args,
            result=result,
        )
        self._log.append(entry)

    def state_message(self) -> dict[str, Any]:
        """The state of every camera, as sent on the WebSocket."""
        now = self.now()
        message = StateMessage(
            ts=now.iso, cameras={camera.id: camera.state(now) for camera in self.cameras.values()}
        )
        return message.model_dump(mode="json")

    def send_pending_events(self) -> None:
        """Send events the cameras produced since last time (held back while silent)."""
        if self.silent:
            return
        timestamp = self.now().iso
        for camera in self.cameras.values():
            for event, detail in camera.pending_events:
                message = EventMessage(
                    ts=timestamp, camera_id=camera.id, event=event, detail=detail
                )
                self._broadcast(message.model_dump(mode="json"))
            camera.pending_events.clear()

    def add_subscriber(self) -> SubscriberQueue:
        """A queue that will receive every message for one new WebSocket."""
        queue: SubscriberQueue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: SubscriberQueue) -> None:
        """Forget a WebSocket that has closed."""
        self._subscribers.discard(queue)

    def drop_websockets(self) -> None:
        """Failure switch: close every open WebSocket, as if the agent's network blipped."""
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    def _broadcast(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(message)

    async def _push_state_forever(self) -> None:
        while True:
            now = self.now()
            for camera in self.cameras.values():
                camera.advance(now)
            if not self.silent:
                self.send_pending_events()
                self._broadcast(self.state_message())
            await self.clock.sleep(PUSH_INTERVAL_SECONDS)


def create_fake_agent_app(agent: FakeAgent) -> FastAPI:
    """The FastAPI app serving `agent`; pushes start when the app starts serving."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await agent.start()
        try:
            yield
        finally:
            await agent.stop()

    app = FastAPI(title="Fake camera agent", lifespan=lifespan)
    app.state.agent = agent
    add_fake_agent_routes(app, agent)
    return app


def main(argv: list[str] | None = None) -> None:
    """Command line entry point: serve a fake agent on the network."""
    parser = argparse.ArgumentParser(description="Serve a fake camera-laptop agent.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=AGENT_PORT, help="port to listen on")
    parser.add_argument("--cameras", type=int, default=2, help="number of simulated PTZs")
    parser.add_argument("--crop-cameras", type=int, default=0, help="number of crop cameras")
    parser.add_argument("--token", default="", help="require this X-Agent-Token")
    parser.add_argument("--agent-id", default="fake-agent", help="agent_id in /agent/info")
    parser.add_argument("--name", default="Fake agent", help="name in /agent/info")
    parser.add_argument(
        "--contract-version", default=CONTRACT_VERSION, help="claim another contract version"
    )
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    agent = FakeAgent(
        SystemClock(),
        ptz_cameras=options.cameras,
        crop_cameras=options.crop_cameras,
        token=options.token,
        agent_id=options.agent_id,
        name=options.name,
        contract_version=options.contract_version,
    )
    uvicorn.run(create_fake_agent_app(agent), host=options.host, port=options.port)


if __name__ == "__main__":
    main()
