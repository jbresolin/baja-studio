"""Transport and slow motion for the clip on air, and the replay speed of the next cue (WP5c).

Transport (`transport {action}`) acts on the *program* source only; the
cued clip is never touched. A manual pause is not an end: auto-play and
on-air end actions fire on a take and on a natural end only, and a restart
is a seek to 0 plus play, not a new airing. Pausing on air is logged with
the client that did it.

Slow motion is OBS's `speed_percent`, with the clip's audio muted below
100 % (`parking.py`). For a cue, it is set when the clip is loaded. For the
clip on air (`set_program_speed`) there are two ways, chosen by the setting
`obs.live_speed_change`, both in `playout/live_speed.py`:

- `reload`: reload the source on air at the new speed and put it back
  where it should be. OBS reopens the file, so program shows a hiccup.
- `swap` (default): park the same clip at the new speed in the other
  playback source just ahead of the frame on air, cut when program gets
  there. The double buffer makes this seamless; a clip cued there is cued
  again afterwards.

The result of each change is logged at INFO, every step at DEBUG. One change
at a time, the last wish winning, is `modules/playout/speed_change.py`.
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
from baja_studio.playout.live_speed import LiveSpeedChange
from baja_studio.playout.parking import (
    NORMAL_SPEED_PERCENT,
    SPEEDS_PERCENT,
    ParkError,
    frames_per_second,
)
from baja_studio.playout.switcher import Cued, CueError, ProgramSwitcher, SwitchError

TRANSPORT_ACTIONS = ("play", "pause", "toggle", "restart", "seek", "step", "nudge")


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
        self._live_speed = LiveSpeedChange(switcher, clock)
        # Numbers each live speed change in the logs.
        self._speed_changes = 0
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
        """Change the speed of the clip on air, by the configured method.

        Raises `SwitchError` when it could not be done; with swap, the clip
        on air then plays on at its old speed (nothing was cut).
        """
        program = self._switcher.program
        if program is None:
            raise SwitchError("nothing from playout is on air")
        if program.speed_percent == speed_percent:
            return
        method = self._settings().obs.live_speed_change
        self._speed_changes += 1
        number, old_speed = self._speed_changes, program.speed_percent
        began_at = self._clock.monotonic()
        try:
            if method == "reload":
                self._live_speed.reload(speed_percent, number)
            else:
                self._swap_at_speed(speed_percent, number)
        except (ParkError, ObsError, SwitchError) as error:
            self._logger.info(
                "%r speed %d %% → %d %% on air (%s, change %d) by %s FAILED in %.2f s: %s",
                program.clip["title"],
                old_speed,
                speed_percent,
                method,
                number,
                source,
                self._clock.monotonic() - began_at,
                error,
            )
            if isinstance(error, SwitchError):
                raise
            raise SwitchError(f"speed change failed, still at {old_speed} %: {error}") from error
        program.speed_percent = speed_percent
        self._save_session()
        self._logger.info(
            "%r speed %d %% → %d %% on air (%s, change %d) by %s in %.2f s",
            program.clip["title"],
            old_speed,
            speed_percent,
            method,
            number,
            source,
            self._clock.monotonic() - began_at,
        )

    def _swap_at_speed(self, speed_percent: int, number: int) -> None:
        """Swap to the other source at the new speed; a clip cued there is cued again after."""
        switcher = self._switcher
        # A clip cued there is overwritten; it is cued again once the swap is done.
        displaced, switcher.cued = switcher.cued, None
        try:
            self._live_speed.swap(speed_percent, number)
        finally:
            if displaced is not None:
                self._recue(displaced)

    def _recue(self, displaced: Cued) -> None:
        try:
            self._switcher.cue(displaced.clip, displaced.playlist_id, displaced.speed_percent)
        except (ObsError, SwitchError) as error:
            self._cue_lost(displaced, str(error))
