"""Changing the speed of the clip on air, step by step, each step confirmed by OBS.

The WP5c bench (OBS 32, LIVESTACK-56) showed the first version racing OBS:
it trusted OBS's status ("playing" comes before the first frame), so it
sometimes cut to a black source, sought a file that had no frame yet (the
seek is ignored and the clip restarts from 0), or reused a "started" event
from an earlier open. Both methods now go through explicit states, and give
up cleanly if OBS has not done its part within 2 s.

**Swap** (default), the clip on air keeps playing untouched until the cut:

1. load the clip at the new speed in the off-air source, forced
   (`MediaParker.load`), and wait for *its* start;
2. pause it, and confirm the pause;
3. seek it to a quarter of a second (on air) *ahead* of the frame on air,
   and read the position back (retried up to 3 times);
4. wait until the clip on air reaches that frame (if it already passed it,
   park it ahead again);
5. only then cut, play (unless the clip was paused) and stop the old source.

Parking ahead and waiting is what puts the new source on the very frame
program is showing when the cut happens; a seek takes OBS a moment, so
aiming at "now" would always land behind.

On a timeout or a refusal nothing is cut: the clip on air plays on at its
old speed, and the error goes to the operator.

**Reload** changes the source on air: forced reload at the new speed (OBS
shows black until the first frame), wait for its start, pause, seek ahead
of where the clip would be by now at the new speed, confirm, wait until
that moment, play. Program does show the reload; the position is right.

Runs under the controller's lock, like every switching action.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.playout.parking import ParkError, frames_per_second
from baja_studio.playout.switcher import ProgramSwitcher, SwitchError

# A whole live speed change must be done within this long, or nothing is cut.
SPEED_CHANGE_TIMEOUT_SECONDS = 2.0
# The new source is parked this far ahead (in time on air) of the frame on air.
LEAD_SECONDS = 0.25
# Park ahead at most this many times when program keeps overtaking the parked frame.
PARK_ATTEMPTS = 3
# Program has reached the parked frame when it is this close (OBS speaks milliseconds).
REACHED_TOLERANCE_SECONDS = 0.001

logger = logging.getLogger(__name__)


class LiveSpeedChange:
    """Swap or reload the clip on air at a new speed; `number` names one change in the logs."""

    def __init__(self, switcher: ProgramSwitcher, clock: Clock) -> None:
        """Every method is called with playout's lock held."""
        self._switcher = switcher
        self._parker = switcher.parker
        self._obs = switcher.parker.obs
        self._clock = clock
        self._never = threading.Event()

    def swap(self, speed_percent: int, number: int) -> None:
        """Park the clip on air at `speed_percent` in the other source, then cut to it.

        Raises `ParkError` or `SwitchError` (nothing cut) when it cannot.
        """
        switcher, parker = self._switcher, self._parker
        program = switcher.program
        assert program is not None
        clip, title = program.clip, program.clip["title"]
        deadline = self._clock.monotonic() + SPEED_CHANGE_TIMEOUT_SECONDS
        program_source = switcher.program_source()
        target = switcher.off_air_slot()
        target_source = switcher.slot(target).source
        old_speed = program.speed_percent / 100
        try:
            request = parker.load(target_source, clip, speed_percent, deadline)
            logger.debug("speed change %d: %s loaded (request %d)", number, target_source, request)
            parker.pause(target_source, title, deadline)
            for attempt in range(1, PARK_ATTEMPTS + 1):
                on_air = self._obs.get_media_input_status(program_source)
                playing = on_air.state == MediaState.PLAYING
                at_s = (on_air.cursor_s or 0.0) + (LEAD_SECONDS * old_speed if playing else 0.0)
                self._refuse_past_the_end(clip, at_s, on_air)
                parked = parker.seek_confirmed(target_source, clip, at_s, deadline)
                logger.debug(
                    "speed change %d: %s parked for %.3f s, confirmed %.3f s, program at %.3f s "
                    "(request %d, try %d)",
                    number,
                    target_source,
                    at_s,
                    parked,
                    on_air.cursor_s or 0.0,
                    request,
                    attempt,
                )
                if not playing or self._program_reaches(
                    program_source, parked, old_speed, deadline
                ):
                    break
                logger.debug(
                    "speed change %d: program passed %.3f s; parking again", number, parked
                )
            else:
                raise ParkError(f"program kept passing the parked frame of {title!r}")
            switcher.move_program_to(target, play=playing)
            logger.debug(
                "speed change %d: cut to %s at %.3f s, %s",
                number,
                target_source,
                parked,
                "playing" if playing else "paused",
            )
        except (ParkError, SwitchError, ObsError):
            # Off air: leave it empty-handed rather than half-loaded.
            switcher.stop_slot(target)
            raise

    def reload(self, speed_percent: int, number: int) -> None:
        """Reload the source on air at `speed_percent` and put it back where it should be.

        Raises `ParkError` or `SwitchError` when it cannot; refuses up front
        (touching nothing) when the clip would end before the change is done.
        """
        switcher, parker = self._switcher, self._parker
        program = switcher.program
        assert program is not None
        clip, title = program.clip, program.clip["title"]
        source = switcher.program_source()
        speed = speed_percent / 100
        before = self._obs.get_media_input_status(source)
        was_paused = before.state == MediaState.PAUSED
        position = before.cursor_s or 0.0
        if not was_paused:
            # Where the clip will be by the time the change gives up, plus the lead.
            latest = position + (SPEED_CHANGE_TIMEOUT_SECONDS + LEAD_SECONDS) * speed
            self._refuse_past_the_end(clip, latest, before)
        switched_at = self._clock.monotonic()
        deadline = switched_at + SPEED_CHANGE_TIMEOUT_SECONDS

        def due_position() -> float:
            # Where the clip should be now: it "went on" at its new speed since the reload.
            elapsed = 0.0 if was_paused else self._clock.monotonic() - switched_at
            return position + elapsed * speed

        try:
            request = parker.load(source, clip, speed_percent, deadline)
            logger.debug("speed change %d: %s reloaded (request %d)", number, source, request)
            parker.pause(source, title, deadline)
            for attempt in range(1, PARK_ATTEMPTS + 1):
                at_s = due_position() + (0.0 if was_paused else LEAD_SECONDS * speed)
                parked = parker.seek_confirmed(source, clip, at_s, deadline)
                logger.debug(
                    "speed change %d: %s parked for %.3f s, confirmed %.3f s (request %d, try %d)",
                    number,
                    source,
                    at_s,
                    parked,
                    request,
                    attempt,
                )
                if was_paused:
                    return
                if self._time_reaches(due_position, parked, speed, deadline):
                    self._obs.trigger_media_input_action(source, MediaAction.PLAY)
                    logger.debug("speed change %d: %s playing from %.3f s", number, source, parked)
                    return
            raise ParkError(f"the clip on air kept passing the parked frame of {title!r}")
        except (ParkError, ObsError):
            if not was_paused:
                # The source on air was reloaded: never leave it paused or black.
                self._play_quietly(source)
            raise

    def _program_reaches(self, source: str, parked_s: float, speed: float, deadline: float) -> bool:
        """Wait until the clip on air in `source` reaches `parked_s`; False if already past it."""
        tolerance = REACHED_TOLERANCE_SECONDS
        first = True
        while True:
            status = self._obs.get_media_input_status(source)
            position = status.cursor_s or 0.0
            if position > parked_s + tolerance and first:
                return False
            if position >= parked_s - tolerance or status.state != MediaState.PLAYING:
                return True
            first = False
            self._sleep_until_due((parked_s - position) / speed, deadline)

    def _time_reaches(
        self, due_position: Callable[[], float], parked_s: float, speed: float, deadline: float
    ) -> bool:
        """Wait until `due_position()` reaches `parked_s`; False if it is already past it."""
        if due_position() > parked_s + REACHED_TOLERANCE_SECONDS:
            return False
        while due_position() < parked_s - REACHED_TOLERANCE_SECONDS:
            self._sleep_until_due((parked_s - due_position()) / speed, deadline)
        return True

    def _sleep_until_due(self, seconds: float, deadline: float) -> None:
        if self._clock.monotonic() + seconds > deadline:
            raise ParkError("OBS took too long: the change would finish after 2 s")
        self._clock.wait(self._never, seconds)

    @staticmethod
    def _refuse_past_the_end(clip: dict[str, Any], at_s: float, status: MediaStatus) -> None:
        if status.duration_s is None:
            return
        if status.state == MediaState.ENDED or at_s > status.duration_s - 1 / frames_per_second(
            clip
        ):
            raise SwitchError("too close to the end of the clip to change its speed")

    def _play_quietly(self, source: str) -> None:
        try:
            self._obs.trigger_media_input_action(source, MediaAction.PLAY)
        except ObsError:
            logger.warning("could not resume %s after a failed speed change", source)
