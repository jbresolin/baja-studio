"""Transport and slow motion for the clip on air, and the replay speed of the next cue (WP5c).

Transport (`transport {action}`) acts on the *program* source only; the
cued clip is never touched. A manual pause is not an end: auto-play and
on-air end actions fire on a take and on a natural end only, and a restart
is a seek to 0 plus play, not a new airing. Pausing on air is logged with
the client that did it.

Slow motion is OBS's `speed_percent`, with the clip's audio muted below
100 % (`parking.py`). For a cue, it is set before the restart. For the clip
on air (`set_program_speed`) there are two ways, chosen by the setting
`obs.live_speed_change`:

- `reload`: set the speed on the source on air, wait for OBS to start it
  again, and seek back to where it was (plus the reload time at the new
  speed). OBS reopens the file, so the output may show a hiccup.
- `swap` (default): park the same clip at the new speed in the other
  playback source on the frame on air, then cut to it. The double buffer
  makes this seamless; a clip cued there is cued again afterwards.

Every method runs under the controller's lock (the controller owns it).
"""

import logging
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.playout.config import PlayoutSettings
from baja_studio.playout.parking import NORMAL_SPEED_PERCENT, SPEEDS_PERCENT, ParkError
from baja_studio.playout.switcher import Cued, CueError, ProgramSwitcher, SwitchError

# Used for frame steps when ffprobe gave no frame rate (WP5c §3).
FALLBACK_FPS = 30.0
TRANSPORT_ACTIONS = ("play", "pause", "toggle", "restart", "seek", "step", "nudge")


def frames_per_second(clip: dict[str, Any]) -> float:
    """The clip's frame rate from ffprobe, else 30."""
    fps = clip.get("fps")
    return float(fps) if isinstance(fps, int | float) and fps > 0 else FALLBACK_FPS


def check_speed(speed_percent: object) -> int:
    """A replay speed the page offers (100, 50 or 25), else `SwitchError`."""
    if (
        isinstance(speed_percent, int)
        and not isinstance(speed_percent, bool)
        and speed_percent in SPEEDS_PERCENT
    ):
        return speed_percent
    allowed = ", ".join(str(speed) for speed in SPEEDS_PERCENT)
    raise SwitchError(f"speed must be one of {allowed} (percent)")


class ProgramTransport:
    """Play, pause, seek and speed for the clip on air; replay speed for the next cue."""

    def __init__(
        self,
        switcher: ProgramSwitcher,
        settings: Callable[[], PlayoutSettings],
        clock: Clock,
        logger: logging.LoggerAdapter[logging.Logger],
        save_session: Callable[[], None],
        cue_lost: Callable[[Cued, str], None],
    ) -> None:
        """`save_session` stores and publishes program and cue; `cue_lost` puts a cue back.

        `cue_lost(cued, reason)` is called when a clip that was cued could not
        be cued again (it goes back to the head of the queue, with the reason
        shown as the cue problem).
        """
        self._switcher = switcher
        self._obs = switcher.parker.obs
        self._settings = settings
        self._clock = clock
        self._logger = logger
        self._save_session = save_session
        self._cue_lost = cue_lost
        self.next_cue_speed = NORMAL_SPEED_PERCENT

    # --- transport ------------------------------------------------------------------------

    def control(self, action: str, args: dict[str, Any], source: str) -> MediaStatus:
        """Carry out one transport action on the clip on air; returns OBS's status after it."""
        program_source = self._switcher.program_source()
        program = self._switcher.program
        assert program is not None
        status = self._obs.get_media_input_status(program_source)
        if action == "toggle":
            action = "pause" if status.state == MediaState.PLAYING else "play"
        if action == "play":
            self._obs.trigger_media_input_action(program_source, MediaAction.PLAY)
        elif action == "pause":
            self._obs.trigger_media_input_action(program_source, MediaAction.PAUSE)
            self._logger.info("%r paused on air by %s", program.clip["title"], source)
        elif action == "restart":
            # A seek and play, not OBS's restart (which reopens the file on air).
            self._seek(program_source, program.clip, 0.0, status)
            self._obs.trigger_media_input_action(program_source, MediaAction.PLAY)
        elif action == "seek":
            self._seek(program_source, program.clip, float(args["position_s"]), status)
        elif action == "nudge":
            self._seek(
                program_source, program.clip, (status.cursor_s or 0.0) + args["seconds"], status
            )
        elif action == "step":
            if status.state != MediaState.PAUSED:
                raise SwitchError("pause first: frame steps work on a paused clip")
            frame = 1 / frames_per_second(program.clip)
            self._seek(
                program_source,
                program.clip,
                (status.cursor_s or 0.0) + args["frames"] * frame,
                status,
            )
        else:
            raise SwitchError(f"unknown transport action {action!r}")
        return self._obs.get_media_input_status(program_source)

    def _seek(
        self, source: str, clip: dict[str, Any], position_s: float, status: MediaStatus
    ) -> None:
        """Seek within the clip, never onto its very end (ending is Skip's job)."""
        self._switcher.parker.seek(source, self._clamped(clip, position_s, status), clip["title"])

    @staticmethod
    def _clamped(clip: dict[str, Any], position_s: float, status: MediaStatus) -> float:
        last_frame = (
            status.duration_s - 1 / frames_per_second(clip)
            if status.duration_s is not None
            else position_s
        )
        return max(0.0, min(position_s, last_frame))

    # --- speed ----------------------------------------------------------------------------

    def set_cue_speed(self, speed_percent: int) -> Cued | None:
        """The replay speed for the cued clip (cued again at it), or for the next cue."""
        cued = self._switcher.cued
        if cued is None:
            self.next_cue_speed = speed_percent
            return None
        if cued.speed_percent == speed_percent:
            return cued
        try:
            # The cued clip sits in the off-air source, so this never touches program.
            recued = self._switcher.cue(cued.clip, cued.playlist_id, speed_percent)
        except CueError as error:
            self._switcher.cued = None
            self._cue_lost(cued, str(error))
            self._save_session()
            raise
        self._save_session()
        return recued

    def take_next_cue_speed(self) -> int:
        """The speed a new cue gets; it applies to that one cue only, then 100 % again."""
        speed, self.next_cue_speed = self.next_cue_speed, NORMAL_SPEED_PERCENT
        return speed

    def set_program_speed(self, speed_percent: int, source: str) -> None:
        """Change the speed of the clip on air, by the configured method."""
        program_source = self._switcher.program_source()
        program = self._switcher.program
        assert program is not None
        if program.speed_percent == speed_percent:
            return
        method = self._settings().obs.live_speed_change
        self._logger.info(
            "%r speed %d %% → %d %% on air (%s) by %s",
            program.clip["title"],
            program.speed_percent,
            speed_percent,
            method,
            source,
        )
        try:
            if method == "reload":
                self._reload_at_speed(program_source, program.clip, speed_percent)
            else:
                self._swap_at_speed(program.clip, speed_percent)
        except ParkError as error:
            raise SwitchError(f"speed change failed: {error}") from error
        program.speed_percent = speed_percent
        self._save_session()

    def _reload_at_speed(self, source: str, clip: dict[str, Any], speed_percent: int) -> None:
        """Set the speed on the source on air, wait for OBS to start it again, seek back."""
        parker = self._switcher.parker
        before = self._obs.get_media_input_status(source)
        was_paused = before.state == MediaState.PAUSED
        started_at = self._clock.monotonic()
        with self._obs.expecting_event("MediaInputPlaybackStarted", inputName=source) as start:
            self._obs.set_input_settings(source, {"speed_percent": speed_percent})
            parker.apply_audio_rule(source, speed_percent)
            parker.wait_until_playing(source, start, clip["title"], paused_is_fine=was_paused)
        position = before.cursor_s or 0.0
        if not was_paused:
            # While OBS reopened the file the clip "went on" at its new speed.
            position += (self._clock.monotonic() - started_at) * speed_percent / 100
        parker.seek(source, self._clamped(clip, position, before), clip["title"])
        if was_paused:
            self._obs.trigger_media_input_action(source, MediaAction.PAUSE)

    def _swap_at_speed(self, clip: dict[str, Any], speed_percent: int) -> None:
        """Park the clip at the new speed in the other source on the frame on air, then cut."""
        switcher, parker = self._switcher, self._switcher.parker
        program_source = switcher.program_source()
        target = switcher.off_air_slot()
        target_source = switcher.slot(target).source
        # A clip cued there is overwritten; it is cued again once the swap is done.
        displaced, switcher.cued = switcher.cued, None
        try:
            parker.park(target_source, clip, speed_percent)
            now = self._obs.get_media_input_status(program_source)
            parker.seek(target_source, self._clamped(clip, now.cursor_s or 0.0, now), clip["title"])
            switcher.move_program_to(target, play=now.state != MediaState.PAUSED)
        finally:
            if displaced is not None:
                self._recue(displaced)

    def _recue(self, displaced: Cued) -> None:
        try:
            self._switcher.cue(displaced.clip, displaced.playlist_id, displaced.speed_percent)
        except (ObsError, SwitchError) as error:
            self._cue_lost(displaced, str(error))
