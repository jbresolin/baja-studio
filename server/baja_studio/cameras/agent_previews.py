"""Previews of agent cameras from the agent's own snapshots (contract 1.1, ADR 0023).

An agent camera that lists `snapshot` in its capabilities (a camera node's
crop cameras do) serves `GET /cameras/{id}/snapshot`: usually its whole
source frame, which is just what a crop camera's preview wants under its
window outline. The studio asks for it the way it asks OBS for screenshots
(`previews.py`, ADR 0020): only while a page shows the preview, reused for a
second, one request in flight at a time, and with each agent's own share of
the preview slots, so a slow laptop holds up only its own previews.

A snapshot that shows only the window (a node camera in OBS mode without an
uncropped preview source) is passed on as such, with the window the agent
says it was taken at, and the page pieces the frame together as for an
`obs_crop` camera.
"""

import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from baja_studio.adapters.obs_images import ImageData
from baja_studio.agents.client import AgentError, AgentSnapshot
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.previews import (
    MAX_PREVIEWS_PER_OBS,
    SHOWS_FULL,
    SHOWS_WINDOW,
    Preview,
    PreviewCache,
    PreviewUnavailableError,
)
from baja_studio.core.clock import Clock

# The agent client gives up after 2 s; the page's wait is a little longer.
SNAPSHOT_WAIT_SECONDS = 3.0


@dataclass(frozen=True)
class _AgentImage(ImageData):
    """A snapshot in the cache: the picture, and the window it shows (None: the whole frame)."""

    window: tuple[float, float, float, float] | None = None


class AgentSnapshots:
    """Agent cameras' previews, one cache and one share of the slots per agent."""

    def __init__(self, clock: Clock, agents: AgentGateway) -> None:
        """`agents` is the agents module, which holds each agent's client."""
        self._clock = clock
        self._agents = agents
        self._lock = threading.Lock()
        self._per_agent: dict[str, tuple[PreviewCache, threading.BoundedSemaphore]] = {}

    def preview(self, camera: CameraConfig, width: int) -> Preview:
        """The camera's snapshot as a preview, or `PreviewUnavailableError` saying why not."""
        agent_id, remote = camera.address.agent_id, camera.address.remote_camera_id
        cache, slots = self._of(agent_id)
        if not slots.acquire(blocking=False):
            raise PreviewUnavailableError(503, f"agent {agent_id} is slow to answer; trying again")
        try:
            image, _ = cache.get(remote, width, lambda: None)
        finally:
            slots.release()
        headers = {"Cache-Control": "no-store"}
        window = image.window if isinstance(image, _AgentImage) else None
        if window is None:
            headers["X-Preview-Shows"] = SHOWS_FULL
        else:
            headers["X-Preview-Shows"] = SHOWS_WINDOW
            headers["X-Crop-Window"] = ",".join(f"{part:.6f}" for part in window)
        return Preview(image, headers)

    def _of(self, agent_id: str) -> tuple[PreviewCache, threading.BoundedSemaphore]:
        with self._lock:
            found = self._per_agent.get(agent_id)
            if found is None:
                fetch = lambda remote, width: self._fetch(agent_id, remote, width)  # noqa: E731
                found = (
                    PreviewCache(self._clock, fetch),
                    threading.BoundedSemaphore(MAX_PREVIEWS_PER_OBS),
                )
                self._per_agent[agent_id] = found
            return found

    def _fetch(self, agent_id: str, remote: str, width: int) -> ImageData:
        """Ask the agent (on the agents module's event loop) and wait a little for it."""
        view = self._agents.link(agent_id)
        if view is None or view.status != "ok":
            status = "not configured" if view is None else view.status
            raise PreviewUnavailableError(503, f"agent {agent_id} is {status}")

        async def ask(client: Any) -> AgentSnapshot:
            snapshot: AgentSnapshot = await client.snapshot(remote, width)
            return snapshot

        try:
            snapshot = self._agents.submit(agent_id, ask).result(SNAPSHOT_WAIT_SECONDS)
        except FutureTimeoutError:
            raise PreviewUnavailableError(
                503, f"agent {agent_id} sent no picture in time"
            ) from None
        except AgentError as error:
            raise PreviewUnavailableError(503, f"agent {agent_id}: {error.detail}") from None
        except Exception as error:  # unreachable, closed: the page shows why and asks again
            raise PreviewUnavailableError(503, f"agent {agent_id}: {error}") from None
        window = snapshot.window if snapshot.shows == "window" else None
        return _AgentImage(snapshot.media_type, snapshot.content, window)
