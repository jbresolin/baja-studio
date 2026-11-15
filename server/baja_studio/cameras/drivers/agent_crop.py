"""An agent crop camera's window (`kind=crop` on a laptop agent), for the preview (ADR 0020).

The agent's pushed state has the window's centre and zoom (`position`) but
not its size, which the preview needs to draw it. So `GET /crop` is read
when the agent link comes up and again whenever the pushed position changed
(at most `MIN_READ_INTERVAL_SECONDS` apart while it moves), never on a timer
at rest. The answer is published as the camera's `crop`, in the same keys
an OBS crop camera publishes (`obs_crop_state.py`) where the agent can tell.

Click-to-centre becomes a `PUT /crop` of the same size, centred where the
operator clicked and kept inside the frame; "reset" is the whole frame. The
agent answers with the crop it applied, which is published at once.
"""

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from baja_studio.agents.client import AgentClient
from baja_studio.agents.contract import CropRect
from baja_studio.cameras.crop_window import WindowTarget
from baja_studio.cameras.drivers.agent_gateway import AgentGateway

MIN_READ_INTERVAL_SECONDS = 0.2


def agent_crop_state(rect: CropRect, zoom: float | None) -> dict[str, Any]:
    """The `crop` entry for a window an agent reported."""
    return {
        "x": round(rect.x, 6),
        "y": round(rect.y, 6),
        "w": round(rect.w, 6),
        "h": round(rect.h, 6),
        "centre_x": round(rect.x + rect.w / 2, 6),
        "centre_y": round(rect.y + rect.h / 2, 6),
        "zoom": zoom,
        # The agent's widest window is the whole frame.
        "magnification": round(1.0 / max(rect.w, rect.h), 3),
    }


def target_rect(current: CropRect, target: WindowTarget) -> CropRect:
    """The crop to `PUT` for a jump: the whole frame, or the same size centred on the target."""
    if target.whole:
        return CropRect(x=0.0, y=0.0, w=1.0, h=1.0)
    return CropRect(
        x=min(max(target.centre_x - current.w / 2, 0.0), 1.0 - current.w),
        y=min(max(target.centre_y - current.h / 2, 0.0), 1.0 - current.h),
        w=current.w,
        h=current.h,
    )


class AgentCropMirror:
    """Keeps one agent crop camera's window in the studio's state."""

    def __init__(
        self,
        gateway: AgentGateway,
        agent_id: str,
        remote_camera_id: str,
        publish: Callable[[str, Any], None],
    ) -> None:
        """`publish(key, value)` is the driver host's (it sets the camera's `crop`)."""
        self._gateway = gateway
        self._agent_id = agent_id
        self._remote_id = remote_camera_id
        self._publish = publish
        self.rect: CropRect | None = None
        self._zoom: float | None = None
        self._read_for: Any = ...
        self._read_at = -1e9
        self._reading = False

    def follow(self, linked: bool, position: dict[str, Any] | None, now: float) -> None:
        """Called by the agent driver's follow loop; reads `/crop` when the window moved."""
        if not linked:
            self._read_for = ...  # read again once the link is back
            return
        self._zoom = None if position is None else position.get("zoom")
        if self._reading or position == self._read_for:
            return
        if now - self._read_at < MIN_READ_INTERVAL_SECONDS:
            return
        self._reading, self._read_for, self._read_at = True, position, now
        remote = self._remote_id
        future = self._gateway.submit(self._agent_id, lambda client: client.crop(remote))
        future.add_done_callback(self._read_done)

    def accept(self, rect: CropRect) -> None:
        """Publish a window the agent reported (read, or applied by a `PUT /crop`)."""
        self.rect = rect
        self._publish("crop", agent_crop_state(rect, self._zoom))

    def accept_answer(self, done: "Future[Any]") -> None:
        """Done-callback of a `PUT /crop`: publish the crop the agent applied."""
        if done.exception() is None and isinstance(done.result(), CropRect):
            self.accept(done.result())

    def put_call(self, target: WindowTarget) -> Callable[[AgentClient], Any] | None:
        """The agent call for a jump, or None while the current window is not known."""
        if self.rect is None:
            return None
        rect, remote = target_rect(self.rect, target), self._remote_id

        async def put(client: AgentClient) -> CropRect:
            return await client.set_crop(remote, rect)

        return put

    def _read_done(self, done: "Future[Any]") -> None:
        self._reading = False
        if done.exception() is None and isinstance(done.result(), CropRect):
            self.accept(done.result())
        else:
            self._read_for = ...  # try again on the next look
