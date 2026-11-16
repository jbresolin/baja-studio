"""A node camera's snapshot (contract 1.1, `GET /cameras/{id}/snapshot`), in either video mode.

- `node` mode: the pipeline's newest whole-frame JPEG (about one a second).
- `obs` mode: a screenshot from OBS (`GetSourceScreenshot`), taken on demand
  and reused for a second, one request in flight at a time, exactly as the
  studio's previews do (`cameras/previews.PreviewCache`, ADR 0020). OBS
  renders a source *with* its filters, so the camera's own source shows only
  the window; with a `preview_source` (an uncropped view of the camera in
  OBS) the snapshot is the whole frame. A window-only snapshot says so
  (`shows: window`, and where the window was), so the studio places it on
  the frame as it does for an `obs_crop` camera.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baja_studio.adapters.obs_images import ImageData, decode_data_uri
from baja_studio.agents.contract import ErrorCode
from baja_studio.agents.server import CameraCommandError
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.cameras.previews import PreviewCache, PreviewUnavailableError
from baja_studio.core.clock import Clock

SHOWS_FRAME, SHOWS_WINDOW = "frame", "window"


@dataclass(frozen=True)
class Snapshot:
    """A picture of a camera, and what it shows: the whole frame, or the window at `window`."""

    content: bytes
    media_type: str
    shows: str = SHOWS_FRAME
    window: dict[str, float] | None = None

    def headers(self) -> dict[str, str]:
        """The answer's headers: never cached; a window-only picture says where it was."""
        headers = {"Cache-Control": "no-store", "X-Snapshot-Shows": self.shows}
        if self.window is not None:
            headers["X-Crop-Window"] = ",".join(
                f"{self.window[key]:.6f}" for key in ("x", "y", "w", "h")
            )
        return headers


def no_snapshot(reason: str) -> CameraCommandError:
    """The contract's 404 `no_snapshot`: the camera has no picture yet, and why."""
    return CameraCommandError(ErrorCode.NO_SNAPSHOT, reason)


class ObsSnapshots:
    """Screenshots of one camera's OBS source(s), at most one OBS request a second each."""

    def __init__(self, clock: Clock, gateway: ObsGateway) -> None:
        """`gateway` is the camera's OBS connection (the pool's, shared with its driver)."""
        self._gateway = gateway
        self._cache = PreviewCache(clock, self._fetch)

    def take(
        self,
        source: str,
        preview_source: str,
        width: int,
        window: Callable[[], dict[str, Any] | None],
    ) -> Snapshot:
        """The whole frame (from `preview_source`) or the window (from `source`)."""
        shows_frame = bool(preview_source)
        name = preview_source if shows_frame else source
        if not name:
            raise no_snapshot("no OBS source chosen for this camera")
        try:
            image, seen = self._cache.get(name, width, window)
        except PreviewUnavailableError as unavailable:
            raise no_snapshot(unavailable.detail) from None
        if shows_frame:
            return Snapshot(image.content, image.media_type)
        return Snapshot(image.content, image.media_type, SHOWS_WINDOW, _rect(seen))

    def _fetch(self, source: str, width: int) -> ImageData:
        client, link = self._gateway.client(), self._gateway.link()
        if client is None or not link.connected:
            raise PreviewUnavailableError(503, link.problem)
        return decode_data_uri(client.get_source_screenshot(source, "jpg", width))


def _rect(window: dict[str, Any] | None) -> dict[str, float] | None:
    if window is None or not all(key in window for key in ("x", "y", "w", "h")):
        return None
    return {key: float(window[key]) for key in ("x", "y", "w", "h")}
