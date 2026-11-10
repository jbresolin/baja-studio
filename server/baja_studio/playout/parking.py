"""Loading a clip into a media source and parking it, paused, on a chosen frame.

A cue (park on the first frame of the off-air source) and the "swap" live
speed change (park the clip on air, at its new speed, on the frame on air)
are the same steps: set the file and the speed, restart, wait until OBS
says it plays, pause, seek. OBS refuses to seek a source that has not
started (the 2026-11-08 bench: "media must be playing or paused"), so the
wait is what makes it work; split out of `switcher.py` in WP5c.

Slow motion is OBS's `speed_percent` on the media source. Below 100 % the
clip's audio is muted (its pitch would drop); playout unmutes only a source
it muted itself, so a source the operator muted in OBS stays muted.
"""

import contextlib
import threading
from typing import Any, Protocol

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsRequestFailedError
from baja_studio.core.clock import Clock

# A source must start playing within this long, or the cue fails.
START_TIMEOUT_SECONDS = 3.0
# While waiting for that start, OBS's status is asked again this often.
START_POLL_SECONDS = 0.1
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


class ParkError(Exception):
    """OBS would not start or park a clip; the message carries OBS's words."""


class MediaObs(Protocol):
    """The OBS requests parking needs (`ObsWebsocketClient` provides them)."""

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
        self, event_type: str, **match: Any
    ) -> contextlib.AbstractContextManager[threading.Event]:
        """See `ObsWebsocketClient`."""


class MediaParker:
    """Loads, starts, pauses and seeks media sources; remembers which ones it muted."""

    def __init__(self, obs: MediaObs, clock: Clock) -> None:
        """`obs` is the playout OBS client; every call is made under playout's lock."""
        self.obs = obs
        self._obs = obs
        self._clock = clock
        self._muted_by_us: set[str] = set()

    def park(
        self, source: str, clip: dict[str, Any], speed_percent: int, at_s: float = 0.0
    ) -> None:
        """Load `clip` into `source` at `speed_percent` and leave it paused at `at_s`.

        Raises `ParkError` when the source does not start within 3 s or OBS
        refuses the seek.
        """
        self._obs.set_input_settings(
            source,
            {
                **MEDIA_SOURCE_SETTINGS,
                "local_file": clip["file_path"],
                "speed_percent": speed_percent,
            },
        )
        self.apply_audio_rule(source, speed_percent)
        with self._obs.expecting_event("MediaInputPlaybackStarted", inputName=source) as start:
            self._obs.trigger_media_input_action(source, MediaAction.RESTART)
            self.wait_until_playing(source, start, clip["title"])
        self._obs.trigger_media_input_action(source, MediaAction.PAUSE)
        self.seek(source, at_s, clip["title"])

    def seek(self, source: str, at_s: float, title: str) -> None:
        """Move `source` to `at_s` seconds (it must be playing or paused)."""
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

    def wait_until_playing(
        self, source: str, start: threading.Event, title: str, paused_is_fine: bool = False
    ) -> None:
        """Return once OBS says `source` plays; `start` is set by its started event.

        `paused_is_fine`: a paused source OBS did not reopen also counts (a
        live speed change on a paused clip, if OBS applies speed without a reload).
        """
        settled = (
            {MediaState.PLAYING, MediaState.PAUSED} if paused_is_fine else {MediaState.PLAYING}
        )
        deadline = self._clock.monotonic() + START_TIMEOUT_SECONDS
        while True:
            # The event only wakes us early; the status is the truth, because a
            # started event left over from loading the file can arrive late.
            status = self._obs.get_media_input_status(source)
            if status.state in settled:
                return
            if self._clock.monotonic() >= deadline:
                raise ParkError(
                    f"OBS did not start {title!r} in {source!r} within "
                    f"{START_TIMEOUT_SECONDS:g} s (media state {status.state})"
                )
            self._clock.wait(start, START_POLL_SECONDS)
            start.clear()
