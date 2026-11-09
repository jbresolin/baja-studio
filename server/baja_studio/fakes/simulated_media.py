"""A media source as OBS plays it, reduced to what playout can observe.

Pure code told `now` on every call, like the fake agent's simulated camera,
so its rules are tested without any clock. Like real OBS, loading a new file
(or restarting one) does not play it at once: the source is *opening* until
the next tick, which starts it and sends `MediaInputPlaybackStarted`. Until
then it cannot be paused or sought (real OBS refuses `SetMediaInputCursor`
with "media must be playing or paused"), which is why playout waits for the
start before parking a cued clip on its first frame. A `stuck` source never
finishes opening, for the cue-timeout tests.
"""

from dataclasses import dataclass

from baja_studio.adapters.obs_protocol import MediaState


@dataclass
class SimulatedMedia:
    """One media input's file and playback position."""

    file: str = ""
    duration_s: float | None = None
    state: str = MediaState.NONE
    # Position at `anchor_time`; while playing, it grows with time from there.
    anchor_cursor_s: float = 0.0
    anchor_time: float = 0.0
    # Never finishes opening (a file OBS cannot start in time).
    stuck: bool = False

    @property
    def is_seekable(self) -> bool:
        """True while playing or paused: the only states OBS lets a client seek in."""
        return self.state in (MediaState.PLAYING, MediaState.PAUSED)

    def cursor(self, now: float) -> float:
        """Where playback is at `now`, in seconds (never past the end)."""
        if self.state != MediaState.PLAYING:
            return self.anchor_cursor_s
        position = self.anchor_cursor_s + (now - self.anchor_time)
        return min(position, self.duration_s) if self.duration_s is not None else position

    def load(self, file: str, duration_s: float | None, now: float) -> None:
        """A new `local_file`: OBS opens it, and starts it from the top on the next tick."""
        self.file, self.duration_s = file, duration_s
        if file:
            self._set(MediaState.OPENING, 0.0, now)
        else:
            self._set(MediaState.NONE, 0.0, now)

    def restart(self, now: float) -> None:
        """Play from the top (after opening again, like real OBS)."""
        if self.file:
            self._set(MediaState.OPENING, 0.0, now)

    def play(self, now: float) -> None:
        """Resume a paused file; an ended or stopped one starts again from the top."""
        if not self.file:
            return
        if self.state == MediaState.PAUSED:
            self._set(MediaState.PLAYING, self.anchor_cursor_s, now)
        elif self.state not in (MediaState.PLAYING, MediaState.OPENING):
            self._set(MediaState.OPENING, 0.0, now)

    def pause(self, now: float) -> None:
        """Freeze at the current frame (an opening source has no frame yet: nothing happens)."""
        if self.state == MediaState.PLAYING:
            self._set(MediaState.PAUSED, self.cursor(now), now)

    def stop(self, now: float) -> None:
        """Stop and rewind (OBS shows nothing for a stopped source)."""
        if self.file:
            self._set(MediaState.STOPPED, 0.0, now)

    def seek(self, cursor_s: float, now: float) -> None:
        """Jump to `cursor_s`, keeping the play/pause state."""
        limit = self.duration_s if self.duration_s is not None else cursor_s
        self._set(self.state, max(0.0, min(cursor_s, limit)), now)

    def advance(self, now: float) -> str | None:
        """Let time pass; returns "started" or "ended" when that just happened, else None.

        An opening source starts playing from the top. A playing one that
        reached its end says "ended" exactly once.
        """
        if self.state == MediaState.OPENING and not self.stuck:
            self._set(MediaState.PLAYING, 0.0, now)
            return "started"
        if self.state != MediaState.PLAYING or self.duration_s is None:
            return None
        if self.cursor(now) < self.duration_s:
            return None
        self._set(MediaState.ENDED, self.duration_s, now)
        return "ended"

    def _set(self, state: str, cursor_s: float, now: float) -> None:
        self.state, self.anchor_cursor_s, self.anchor_time = state, cursor_s, now
