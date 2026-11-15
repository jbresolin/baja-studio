"""Camera previews: a small still of each camera's OBS source, about once a second (ADR 0020).

Previews are fetched **only on demand**: a page that shows a preview asks
`GET /api/cameras/preview/<camera>` again after each image arrived, and
nothing is asked of OBS while no page looks. For each OBS source and width:

- an image (or OBS's refusal) is reused for `MAX_AGE_SECONDS` (1 s), however
  many pages ask, so ten phones cost OBS one screenshot a second;
- at most one request is in flight, however long OBS takes: a page asking
  meanwhile waits for it, and the second counts from OBS's answer;
- at most `MAX_PREVIEWS_AT_ONCE` previews are served at once (each waits in
  one of the web server's threads), so a hung OBS cannot use them all up.

`GetSourceScreenshot` renders a source *with* its filters, so an `obs_crop`
camera's screenshot is its window, not the whole picture. Unless the camera
names a `preview_source` (an unfiltered view of the same camera), the answer
says it shows the window (`X-Preview-Shows: window`) and carries where the
window was when the screenshot was taken (`X-Crop-Window: x,y,w,h`), so the
page can place it on the whole frame and remember what it saw around it.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_images import ImageData, ImageDataError, decode_data_uri
from baja_studio.adapters.obs_transport import ObsError
from baja_studio.cameras.config import CamerasSettings, DriverKind
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.core.clock import Clock

MAX_AGE_SECONDS = 1.0
# A cache entry nobody asked for in this long is dropped.
FORGET_AFTER_SECONDS = 30.0
# How long a page waits for another page's request to OBS (OBS's own timeout is 2 s).
WAIT_FOR_OTHER_SECONDS = 5.0
# Preview requests served at once. Each waits on OBS in one of the web server's
# worker threads; with OBS hung, many phones must not take them all.
MAX_PREVIEWS_AT_ONCE = 8
SHOWS_WINDOW, SHOWS_FULL = "window", "full"

Fetch = Callable[[str, int], ImageData]


class PreviewUnavailableError(Exception):
    """No preview now; `status_code` and the message are what the page gets."""

    def __init__(self, status_code: int, detail: str) -> None:
        """404: nothing to show for this camera; 503: OBS is away or refused."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class _Entry:
    """One source's last screenshot (or refusal), and the camera's window when it was asked."""

    # When OBS answered (None while the request is in flight).
    taken_at: float | None = None
    image: ImageData | None = None
    error: PreviewUnavailableError | None = None
    window: dict[str, Any] | None = None
    done: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class Preview:
    """What the preview route sends: the image, and the headers that explain it."""

    image: ImageData
    headers: dict[str, str]


class PreviewCache:
    """Screenshots by (source, width): reused for a second, one request in flight each."""

    def __init__(self, clock: Clock, fetch: Fetch) -> None:
        """`fetch(source, width)` asks OBS for one screenshot (raising on failure)."""
        self._clock = clock
        self._fetch = fetch
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, int], _Entry] = {}

    def get(
        self, source: str, width: int, window: Callable[[], dict[str, Any] | None]
    ) -> tuple[ImageData, dict[str, Any] | None]:
        """A screenshot no older than a second, and the `window()` read just before taking it.

        Raises `PreviewUnavailableError` (OBS refused, or did not answer).
        """
        key = (source, width)
        with self._lock:
            now = self._clock.monotonic()
            self._forget_old(now)
            entry = self._entries.get(key)
            # In flight (however long OBS takes), or answered less than a second ago.
            usable = entry is not None and (
                entry.taken_at is None or now - entry.taken_at < MAX_AGE_SECONDS
            )
            leader = not usable
            if leader:
                entry = self._entries[key] = _Entry()
        assert entry is not None
        if leader:
            self._take(entry, source, width, window)
        elif not self._clock.wait(entry.done, WAIT_FOR_OTHER_SECONDS):
            raise PreviewUnavailableError(503, "OBS is slow to answer")
        if entry.error is not None:
            raise entry.error
        assert entry.image is not None
        return entry.image, entry.window

    def _take(
        self,
        entry: _Entry,
        source: str,
        width: int,
        window: Callable[[], dict[str, Any] | None],
    ) -> None:
        try:
            entry.window = window()
            entry.image = self._fetch(source, width)
        except PreviewUnavailableError as error:
            entry.error = error
        except ObsError as error:
            entry.error = PreviewUnavailableError(503, f"OBS: {error}")
        except ImageDataError as error:
            entry.error = PreviewUnavailableError(503, f"OBS sent no usable image: {error}")
        finally:
            with self._lock:
                entry.taken_at = self._clock.monotonic()
            entry.done.set()

    def _forget_old(self, now: float) -> None:
        old = [
            key
            for key, entry in self._entries.items()
            if entry.taken_at is not None and now - entry.taken_at > FORGET_AFTER_SECONDS
        ]
        for key in old:
            del self._entries[key]


class CameraPreviews:
    """Which source to show for a camera, and the preview of it (for the route)."""

    def __init__(
        self,
        clock: Clock,
        obs: ObsGateway,
        settings: Callable[[], CamerasSettings],
        camera_state: Callable[[str], dict[str, Any] | None],
    ) -> None:
        """`camera_state(id)` reads `state.cameras.cameras.<id>` (its `crop` window)."""
        self._obs = obs
        self._settings = settings
        self._camera_state = camera_state
        self._cache = PreviewCache(clock, self._screenshot)
        self._slots = threading.BoundedSemaphore(MAX_PREVIEWS_AT_ONCE)

    def preview(self, camera_id: str, width: int) -> Preview:
        """The camera's preview, or `PreviewUnavailableError` saying why not.

        At most `MAX_PREVIEWS_AT_ONCE` are served at a time; beyond that the
        page is told to try again (it does, a second later).
        """
        if not self._slots.acquire(blocking=False):
            raise PreviewUnavailableError(503, "too many previews at once; trying again")
        try:
            return self._preview(camera_id, width)
        finally:
            self._slots.release()

    def _preview(self, camera_id: str, width: int) -> Preview:
        camera = next((c for c in self._settings().cameras if c.id == camera_id), None)
        if camera is None:
            raise PreviewUnavailableError(404, f"no camera called {camera_id!r}")
        is_obs_crop = camera.driver == DriverKind.OBS_CROP
        if is_obs_crop and camera.crop.preview_source:
            source, shows = camera.crop.preview_source, SHOWS_FULL
        elif camera.obs_source:
            # An agent crop camera's source (its laptop's output) is its window too.
            crop_kind = (self._camera_state(camera_id) or {}).get("capabilities", {}).get("kind")
            shows = SHOWS_WINDOW if is_obs_crop or crop_kind == "crop" else SHOWS_FULL
            source = camera.obs_source
        else:
            raise PreviewUnavailableError(404, "no OBS source set for this camera (Camera setup)")
        link = self._obs.link()
        if not link.connected:
            raise PreviewUnavailableError(503, f"OBS offline: {link.reason}")
        image, window = self._cache.get(source, width, lambda: self._window(camera_id))
        headers = {"X-Preview-Shows": shows, "Cache-Control": "no-store"}
        if window is not None:
            headers["X-Crop-Window"] = ",".join(
                f"{window[key]:.6f}" for key in ("x", "y", "w", "h")
            )
        return Preview(image, headers)

    def _window(self, camera_id: str) -> dict[str, Any] | None:
        crop = (self._camera_state(camera_id) or {}).get("crop")
        if isinstance(crop, dict) and all(key in crop for key in ("x", "y", "w", "h")):
            return crop
        return None

    def _screenshot(self, source: str, width: int) -> ImageData:
        client = self._obs.client()
        if client is None:
            raise PreviewUnavailableError(503, f"OBS offline: {self._obs.link().reason}")
        return decode_data_uri(client.get_source_screenshot(source, "jpg", width))
