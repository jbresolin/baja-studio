"""Slow motion on the clip on air: a replay that jumps back, never a seamless change.

Why not seamless: OBS's media source can only seek to a keyframe, and only
reliably while playing (the OBS probe on the studio PC, LIVESTACK-56). A
speed change that kept the picture exactly where it was needed a seek to an
exact frame, so it failed on air ("OBS did not move ... to 6.52 s (it is at
0.17 s)"). João chose the sports-replay behaviour instead:

- **100 % → 50 % or 25 %** is a *replay*: the clip jumps back
  `obs.replay_jump_back_s` (default 3 s) and plays from there, slowly and
  muted. OBS lands on the keyframe at or before that point (within 0.5 s
  for clips rendered since LIVESTACK-56; older clips may land seconds
  earlier, at worst at the start).
- **Back to 100 %, or 50 % ↔ 25 %**, *continues* from about where the clip
  is: OBS lands on the keyframe at or before it, so up to half a second
  repeats.

Both are made in the off-air playback source while the clip on air plays on
untouched, then one cut (the double buffer):

1. load the clip at the new speed in the off-air source, forced, and wait
   for *its* start (a stopped source is sent PLAY, `MediaParker.load`);
2. seek it while it plays, and take where OBS landed (`seek_playing`);
3. pause it, if the clip on air was paused;
4. cut to it (play on), and stop the source left behind.

On a timeout or a refusal nothing is cut: the clip on air plays on at its
old speed, and the error goes to the operator. Runs under the controller's
lock, like every switching action.
"""

import logging
from dataclasses import dataclass

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.playout.parking import NORMAL_SPEED_PERCENT, ParkError
from baja_studio.playout.switcher import ProgramSwitcher, SwitchError

# A whole change must be done within this long, or nothing is cut.
SPEED_CHANGE_TIMEOUT_SECONDS = 3.0
# "Continue" aims this far (in time on air) ahead of the frame on air: the
# time the change takes, so the keyframe OBS lands on is rarely behind it.
CONTINUE_LEAD_SECONDS = 0.5
# A replay or continue must leave at least this much of the clip to play.
END_MARGIN_SECONDS = 1.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeedChangeResult:
    """What a change did: replay or continue, where it aimed, and where OBS landed."""

    kind: str  # "replay" or "continue"
    aimed_s: float
    landed_s: float


def change_kind(old_speed_percent: int, new_speed_percent: int) -> str:
    """ "replay" when slowing down from normal speed, else "continue"."""
    if old_speed_percent == NORMAL_SPEED_PERCENT and new_speed_percent < NORMAL_SPEED_PERCENT:
        return "replay"
    return "continue"


def aim(kind: str, position_s: float, playing: bool, old_speed: float, jump_back_s: float) -> float:
    """Where in the clip a change seeks to, before OBS rounds it down to a keyframe.

    A replay goes `jump_back_s` back from the frame on air (not before 0).
    A continue goes a little ahead: the clip on air keeps moving at
    `old_speed` (1.0, 0.5 …) while the change is made, unless it is paused.
    """
    if kind == "replay":
        return max(0.0, position_s - jump_back_s)
    return position_s + (CONTINUE_LEAD_SECONDS * old_speed if playing else 0.0)


class LiveSpeedChange:
    """Makes one speed change of the clip on air; `number` names it in the logs."""

    def __init__(self, switcher: ProgramSwitcher, clock: Clock) -> None:
        """Every method is called with playout's lock held."""
        self._switcher = switcher
        self._parker = switcher.parker
        self._obs = switcher.parker.obs
        self._clock = clock

    def change(self, speed_percent: int, jump_back_s: float, number: int) -> SpeedChangeResult:
        """Replay or continue the clip on air at `speed_percent` from the other source.

        Raises `ParkError`, `SwitchError` or `ObsError` (nothing cut) when it cannot.
        """
        switcher, parker = self._switcher, self._parker
        program = switcher.program
        assert program is not None
        clip = program.clip
        deadline = self._clock.monotonic() + SPEED_CHANGE_TIMEOUT_SECONDS
        on_air = self._obs.get_media_input_status(switcher.program_source())
        if on_air.state == MediaState.ENDED:
            raise SwitchError("the clip has ended: nothing to slow down")
        playing = on_air.state == MediaState.PLAYING
        position = on_air.cursor_s or 0.0
        kind = change_kind(program.speed_percent, speed_percent)
        aimed = aim(kind, position, playing, program.speed_percent / 100, jump_back_s)
        if on_air.duration_s is not None and aimed > on_air.duration_s - END_MARGIN_SECONDS:
            raise SwitchError("too close to the end of the clip to change its speed")
        target = switcher.off_air_slot()
        target_source = switcher.slot(target).source
        try:
            request = parker.load(target_source, clip, speed_percent, deadline)
            logger.debug("speed change %d: %s loaded (request %d)", number, target_source, request)
            landed = parker.seek_playing(target_source, clip, aimed, speed_percent / 100, deadline)
            if not playing:
                parker.pause(target_source, clip["title"], deadline)
            switcher.move_program_to(target, play=playing)
        except (ParkError, SwitchError, ObsError):
            # Off air: leave it empty-handed rather than half-loaded.
            switcher.stop_slot(target)
            raise
        logger.debug(
            "speed change %d: %s from %.3f s (aimed %.3f s, program was at %.3f s), cut to %s, %s",
            number,
            kind,
            landed,
            aimed,
            position,
            target_source,
            "playing" if playing else "paused",
        )
        return SpeedChangeResult(kind, aimed, landed)
