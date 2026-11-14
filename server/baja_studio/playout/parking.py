"""Loading a clip into a media source, parking it, and seeking it, as real OBS allows.

What OBS 33 does (measured on the studio PC with an OBS probe, LIVESTACK-56):

- A seek lands on the **keyframe at or before** the position asked for.
  The pipeline renders a keyframe every 0.5 s; older clips have one every
  8.333 s. So a position is never exact, and "read the position back and
  compare to one frame" cannot work except at 0, which is always a keyframe.
- A seek works on a playing source; on a paused one it is not reliable.
- Loading a file into a **stopped** source does not start it; PLAY does.
- "Started" (`MediaInputPlaybackStarted`) arrives just after the reply to
  the load; a seek sent before it is ignored.
- RESTART does nothing to a playing source: playout never sends it.

A **cue** parks the clip on its first frame of the off-air source:

1. **Load**, forced: set the file and the speed. OBS reopens a source only
   when its settings change, so a source already holding that file is
   emptied first. A source left stopped (after a take, playout stops the
   source it left) is then sent PLAY, or it would never start.
2. **Wait for the start**: `MediaInputPlaybackStarted` from that source,
   read after the load request's response, so a start left over from an
   earlier open, or of the other source, does not count.
3. **Pause**, until status says paused.
4. **Seek to 0** (a keyframe), read it back; retried up to three times.

A slow-motion replay (`live_speed.py`) loads the same way, then seeks the
source **while it plays** (`seek_playing`) and takes wherever OBS landed.

All of it must finish by a deadline (3 s for a cue; the caller's for a
replay), or `ParkError` says which step OBS did not finish.

Slow motion is OBS's `speed_percent` on the media source. Below 100 % the
clip's audio is muted (its pitch would drop); playout unmutes only a source
it muted itself, so a source the operator muted in OBS stays muted.
"""

import contextlib
import logging
import threading
from typing import Any, Protocol

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsRequestFailedError
from baja_studio.adapters.obs_websocket import ExpectedEvent
from baja_studio.core.clock import Clock

# A whole cue (load, start, pause, seek) must be done within this long.
CUE_TIMEOUT_SECONDS = 3.0
# While waiting for OBS, its status is read again this often.
POLL_SECONDS = 0.02
# A seek is sent at most this many times...
SEEK_ATTEMPTS = 3
# ...each one given this long to show in OBS's status before the next.
SEEK_CONFIRM_SECONDS = 0.25
# A seek on a playing source counts as landed this long after it was sent,
# even when no jump shows (OBS took 15 to 60 ms on the studio PC).
SEEK_SETTLE_SECONDS = 0.3
# A jump of more than this from where the source would otherwise be shows the seek landed.
SEEK_JUMP_SEEN_SECONDS = 0.25
# OBS reports this position for a source parked on its very first frame.
FIRST_FRAME_REPORTED_SECONDS = 0.034
# Used for seek tolerances when ffprobe gave no frame rate (WP5c §3).
FALLBACK_FPS = 30.0
# Media source settings for a clip: play once, keep the last frame (hold), never
# restart on its own when its scene becomes visible.
MEDIA_SOURCE_SETTINGS = {
    "looping": False,
    "restart_on_activate": False,
    "close_when_inactive": False,
    "clear_on_media_end": False,
    "is_local_file": True,
}
NORMAL_SPEED_PERCENT = 100
# The replay speeds offered (WP5c §4); OBS itself accepts 1..200.
SPEEDS_PERCENT = (100, 50, 25)

logger = logging.getLogger(__name__)


class ParkError(Exception):
    """OBS would not start or park a clip; the message carries OBS's words."""


class MediaObs(Protocol):
    """The OBS requests parking needs (`ObsWebsocketClient` provides them)."""

    def get_input_settings(self, input_name: str) -> dict[str, Any]:
        """See `ObsRequestsMixin`."""

    def set_input_settings(
        self, input_name: str, settings: dict[str, Any], overlay: bool = True
    ) -> None:
        """See `ObsRequestsMixin`."""

    def set_input_mute(self, input_name: str, muted: bool) -> None:
        """See `ObsRequestsMixin`."""

    def trigger_media_input_action(self, input_name: str, action: str) -> None:
        """See `ObsRequestsMixin`."""

    def set_media_input_cursor(self, input_name: str, cursor_s: float) -> None:
        """See `ObsRequestsMixin`."""

    def get_media_input_status(self, input_name: str) -> MediaStatus:
        """See `ObsRequestsMixin`."""

    def expecting_event(
        self, event_type: str, *, after_next_request: bool = False, **match: Any
    ) -> contextlib.AbstractContextManager[ExpectedEvent]:
        """See `ObsWebsocketClient`."""


def frames_per_second(clip: dict[str, Any]) -> float:
    """The clip's frame rate from ffprobe, else 30."""
    fps = clip.get("fps")
    return float(fps) if isinstance(fps, int | float) and fps > 0 else FALLBACK_FPS


class MediaParker:
    """Loads, starts, pauses and seeks media sources; remembers which ones it muted."""

    def __init__(self, obs: MediaObs, clock: Clock) -> None:
        """`obs` is the playout OBS client; every call is made under playout's lock."""
        self.obs = obs
        self._obs = obs
        self._clock = clock
        self._muted_by_us: set[str] = set()
        # Never set: waiting on it is a plain pause on the clock.
        self._never = threading.Event()

    def park(
        self,
        source: str,
        clip: dict[str, Any],
        speed_percent: int,
        at_s: float = 0.0,
        deadline: float | None = None,
    ) -> float:
        """Load `clip` into `source` at `speed_percent`, paused at `at_s`; returns where it is.

        Raises `ParkError` when a step is not done by `deadline` (default:
        3 s from now) or OBS refuses one.
        """
        if deadline is None:
            deadline = self._clock.monotonic() + CUE_TIMEOUT_SECONDS
        self.load(source, clip, speed_percent, deadline)
        self.pause(source, clip["title"], deadline)
        return self.seek_confirmed(source, clip, at_s, deadline)

    def load(self, source: str, clip: dict[str, Any], speed_percent: int, deadline: float) -> int:
        """Steps 1 and 2: open `clip` afresh in `source` and wait for its start.

        Returns the number of the load request (its start was read after it).
        """
        title = clip["title"]
        # A stopped (or ended) source does not start on new settings; it needs PLAY.
        needs_play = self._obs.get_media_input_status(source).state in (
            MediaState.STOPPED,
            MediaState.ENDED,
        )
        if self._obs.get_input_settings(source).get("local_file") == clip["file_path"]:
            # Same file: OBS would reopen nothing (or only on a new speed). Empty it first.
            self._obs.set_input_settings(source, {"local_file": ""})
            logger.debug("%s: emptied (it held %r already)", source, title)
        # Muted before it opens, so not one slowed-down sound is heard.
        self.apply_audio_rule(source, speed_percent)
        settings = {
            **MEDIA_SOURCE_SETTINGS,
            "local_file": clip["file_path"],
            "speed_percent": speed_percent,
        }
        with self._obs.expecting_event(
            "MediaInputPlaybackStarted", after_next_request=True, inputName=source
        ) as started:
            self._obs.set_input_settings(source, settings)
            request = started.request_number or 0
            if needs_play:
                self._obs.trigger_media_input_action(source, MediaAction.PLAY)
            logger.debug(
                "%s: loading %r at %d %% (request %d%s)",
                source,
                title,
                speed_percent,
                request,
                ", PLAY: it was stopped" if needs_play else "",
            )
            self._wait_for_start(source, title, started, deadline)
        logger.debug("%s: started (request %d)", source, request)
        return request

    def pause(self, source: str, title: str, deadline: float) -> None:
        """Step 3: pause `source` and wait until OBS says it is paused."""
        while True:
            self._obs.trigger_media_input_action(source, MediaAction.PAUSE)
            status = self._obs.get_media_input_status(source)
            if status.state == MediaState.PAUSED:
                logger.debug("%s: paused at %.3f s", source, status.cursor_s or 0.0)
                return
            if self._clock.monotonic() >= deadline:
                raise ParkError(f"OBS did not pause {title!r} in {source!r} ({status.state})")
            self._clock.wait(self._never, POLL_SECONDS)

    def seek_confirmed(
        self, source: str, clip: dict[str, Any], at_s: float, deadline: float
    ) -> float:
        """Step 4 of a cue: seek a paused `source` to `at_s`, read it back, try again (up to 3).

        Only for positions that are keyframes (0, the first frame): OBS lands
        anywhere else on the keyframe before. Returns the position OBS
        reports, within one frame of `at_s`.
        """
        # OBS on the studio PC reported 0.033 s after a seek to 0 (its first
        # frame at 30 fps), so never less than that, even for 60 fps clips.
        tolerance = max(1 / frames_per_second(clip), FIRST_FRAME_REPORTED_SECONDS)
        position: float | None = None
        for attempt in range(1, SEEK_ATTEMPTS + 1):
            self.seek(source, at_s, clip["title"])
            confirm_by = min(self._clock.monotonic() + SEEK_CONFIRM_SECONDS, deadline)
            while True:
                position = self._obs.get_media_input_status(source).cursor_s
                if position is not None and abs(position - at_s) <= tolerance:
                    logger.debug(
                        "%s: seek to %.3f s confirmed at %.3f s (try %d)",
                        source,
                        at_s,
                        position,
                        attempt,
                    )
                    return position
                if self._clock.monotonic() >= confirm_by:
                    break
                self._clock.wait(self._never, POLL_SECONDS)
            logger.debug(
                "%s: seek to %.3f s not seen (at %s) on try %d", source, at_s, position, attempt
            )
            if self._clock.monotonic() >= deadline:
                break
        shown = "nowhere" if position is None else f"{position:.2f} s"
        raise ParkError(f"OBS did not move {clip['title']!r} to {at_s:.2f} s (it is at {shown})")

    def seek_playing(
        self, source: str, clip: dict[str, Any], at_s: float, speed: float, deadline: float
    ) -> float:
        """Seek a *playing* `source` towards `at_s`; returns where OBS landed.

        OBS lands on the keyframe at or before `at_s`, so any position up to
        `at_s` (plus what the clip played since) is a landing. It has landed
        once the position jumps away from where playback alone would have
        taken it, or, when the keyframe is so close that no jump shows,
        `SEEK_SETTLE_SECONDS` after the seek. `speed` is the source's speed
        (0.5 at 50 %). Retried up to three times; `ParkError` when it never lands.
        """
        position: float | None = None
        for attempt in range(1, SEEK_ATTEMPTS + 1):
            before = self._obs.get_media_input_status(source).cursor_s or 0.0
            sent_at = self._clock.monotonic()
            self.seek(source, at_s, clip["title"])
            while True:
                status = self._obs.get_media_input_status(source)
                position = status.cursor_s
                elapsed = self._clock.monotonic() - sent_at
                if position is not None and position <= at_s + SEEK_JUMP_SEEN_SECONDS + (
                    elapsed * speed
                ):
                    without_seek = before + elapsed * speed
                    jumped = abs(position - without_seek) > SEEK_JUMP_SEEN_SECONDS
                    if jumped or elapsed >= SEEK_SETTLE_SECONDS:
                        logger.debug(
                            "%s: seek to %.3f s landed at %.3f s (%s, try %d)",
                            source,
                            at_s,
                            position,
                            "jump seen" if jumped else "settled",
                            attempt,
                        )
                        return position
                if self._clock.monotonic() >= deadline or elapsed >= SEEK_SETTLE_SECONDS:
                    break
                self._clock.wait(self._never, POLL_SECONDS)
            logger.debug(
                "%s: seek to %.3f s not landed (at %s) on try %d", source, at_s, position, attempt
            )
            if self._clock.monotonic() >= deadline:
                break
        shown = "nowhere" if position is None else f"{position:.2f} s"
        raise ParkError(f"OBS did not move {clip['title']!r} to {at_s:.2f} s (it is at {shown})")

    def seek(self, source: str, at_s: float, title: str) -> None:
        """Move `source` to `at_s` seconds, unconfirmed (it must be playing or paused)."""
        try:
            self._obs.set_media_input_cursor(source, at_s)
        except ObsRequestFailedError as error:
            raise ParkError(f"OBS would not seek {title!r}: {error.comment}") from error

    def apply_audio_rule(self, source: str, speed_percent: int) -> None:
        """Mute a source playing slower than normal; unmute it if we muted it."""
        if speed_percent < NORMAL_SPEED_PERCENT:
            self._obs.set_input_mute(source, True)
            self._muted_by_us.add(source)
        elif source in self._muted_by_us:
            self._obs.set_input_mute(source, False)
            self._muted_by_us.discard(source)

    def _wait_for_start(
        self, source: str, title: str, started: ExpectedEvent, deadline: float
    ) -> None:
        """Until the start arrives; OBS's status is read meanwhile for the error message."""
        while True:
            status = self._obs.get_media_input_status(source)
            if started.arrived.is_set():
                return
            if self._clock.monotonic() >= deadline:
                raise ParkError(
                    f"OBS did not start {title!r} in {source!r} in time "
                    f"(media state {status.state})"
                )
            self._clock.wait(started.arrived, POLL_SECONDS)
