"""A media source as OBS plays it, reduced to what playout can observe.

Pure code told `now` on every call, like the fake agent's simulated camera,
so its rules are tested without any clock. Like real OBS, opening a file (a
new file, new settings or a restart) goes through three phases:

1. *opening*: status says `OBS_MEDIA_STATE_OPENING`; a seek is refused with
   OBS's words ("must be playing or paused").
2. *loading*: status already says playing (or paused, if paused meanwhile)
   but there is no frame yet. The source shows **black**, and a seek is
   accepted and silently **ignored**: the decoder starts from the top.
3. *first frame*: playback really starts and OBS sends
   `MediaInputPlaybackStarted`.

A seek on a source that has its first frame lands after `seek_s` (OBS seeks
on its media thread); until then status still reports the old position.
It lands on the **keyframe at or before** the position asked for, never on
the position itself: OBS 33 on the studio PC, asked for 5 s in a clip with
keyframes every 8.333 s, went to 0; asked for 10 s, to 8.23 s (the OBS
probe, LIVESTACK-56). `keyframe_interval_s` sets where they are (None:
every position is one, the fake before the probe).

Two more things the probe showed and this models: loading a file (or new
settings) into a *stopped* source does not start it, only PLAY does; and
OBS's RESTART does nothing to a source that is playing or paused.
The phase lengths come from the caller (`FakeObs`, from its seeded timing);
all zero, every phase passes on the next `advance`. A `stuck` source never
finishes opening, for the cue-timeout tests.

`speed` is OBS's `speed_percent` / 100: the position (in the file's own
seconds, as OBS reports it) moves that much per second of real time.
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
    # Playback rate: 1.0 normal, 0.5 for speed_percent 50.
    speed: float = 1.0
    # Opened, but no frame decoded yet (phases 1 and 2 above).
    loading: bool = False
    # When status leaves "opening", and when the first frame arrives.
    ready_at: float = 0.0
    first_frame_at: float = 0.0
    # A seek OBS accepted but has not carried out yet: (position, when).
    pending_seek: tuple[float, float] | None = None
    # A keyframe every this many seconds of the file, from 0 (None: every position).
    keyframe_interval_s: float | None = None

    @property
    def is_seekable(self) -> bool:
        """True while playing or paused: the only states OBS lets a client seek in."""
        return self.state in (MediaState.PLAYING, MediaState.PAUSED)

    @property
    def has_frame(self) -> bool:
        """True when the source shows a picture (false: it is black on screen)."""
        return (
            bool(self.file)
            and not self.loading
            and self.state in (MediaState.PLAYING, MediaState.PAUSED, MediaState.ENDED)
        )

    def cursor(self, now: float) -> float:
        """Where playback is at `now`, in seconds (never past the end)."""
        if self.state != MediaState.PLAYING or self.loading:
            return self.anchor_cursor_s
        position = self.anchor_cursor_s + (now - self.anchor_time) * self.speed
        return min(position, self.duration_s) if self.duration_s is not None else position

    def load(
        self,
        file: str,
        duration_s: float | None,
        now: float,
        opening_s: float = 0.0,
        first_frame_s: float = 0.0,
    ) -> None:
        """A new `local_file`: OBS opens it and starts it from the top (see the phases).

        A stopped source keeps the file but stays stopped (the probe): it
        starts only on PLAY.
        """
        self.file, self.duration_s = file, duration_s
        if file and self.state == MediaState.STOPPED:
            self.loading, self.pending_seek = False, None
            self._set(MediaState.STOPPED, 0.0, now)
        elif file:
            self._open(now, opening_s, first_frame_s)
        else:
            # Emptied. A stopped source stays stopped (the probe), so the next
            # file still needs PLAY; any other is left with nothing.
            self.loading, self.pending_seek = False, None
            emptied = MediaState.STOPPED if self.state == MediaState.STOPPED else MediaState.NONE
            self._set(emptied, 0.0, now)

    def reload(self, now: float, opening_s: float = 0.0, first_frame_s: float = 0.0) -> None:
        """Changed settings (a new speed): OBS opens the file again, from the top.

        The probe (LIVESTACK-56) confirmed it: a new speed reopens the file
        and starts it from 0, with a new started event. A stopped source
        stays stopped.
        """
        if self.file and self.state != MediaState.STOPPED:
            self._open(now, opening_s, first_frame_s)

    def restart(self, now: float, opening_s: float = 0.0, first_frame_s: float = 0.0) -> None:
        """OBS's RESTART: nothing at all while playing or paused (the probe), else from the top."""
        if self.file and self.state not in (MediaState.PLAYING, MediaState.PAUSED):
            self._open(now, opening_s, first_frame_s)

    def play(self, now: float, opening_s: float = 0.0, first_frame_s: float = 0.0) -> None:
        """Resume a paused file; an ended or stopped one starts again from the top."""
        if not self.file:
            return
        if self.state == MediaState.PAUSED:
            self._set(MediaState.PLAYING, self.anchor_cursor_s, now)
        elif self.state not in (MediaState.PLAYING, MediaState.OPENING):
            self._open(now, opening_s, first_frame_s)

    def pause(self, now: float) -> None:
        """Freeze at the current frame (an opening source has no frame yet: nothing happens)."""
        if self.state == MediaState.PLAYING:
            self._set(MediaState.PAUSED, self.cursor(now), now)

    def stop(self, now: float) -> None:
        """Stop and rewind (OBS shows nothing for a stopped source)."""
        if self.file:
            self.loading, self.pending_seek = False, None
            self._set(MediaState.STOPPED, 0.0, now)

    def seek(self, cursor_s: float, now: float, seek_s: float = 0.0) -> None:
        """Jump to the keyframe at or before `cursor_s` after `seek_s`, keeping play/pause.

        Ignored while the first frame is still loading (the decoder starts
        from the top whatever was asked); call only when `is_seekable`.
        """
        if self.loading:
            return
        limit = self.duration_s if self.duration_s is not None else cursor_s
        target = self.keyframe_at_or_before(max(0.0, min(cursor_s, limit)))
        if seek_s <= 0:
            self.pending_seek = None
            self._set(self.state, target, now)
        else:
            self.pending_seek = (target, now + seek_s)

    def keyframe_at_or_before(self, position_s: float) -> float:
        """Where a seek to `position_s` lands: the last keyframe not after it."""
        if self.keyframe_interval_s is None:
            return position_s
        # The small tolerance keeps 8.0 / 0.5 from rounding down to 15.999...
        count = int(position_s / self.keyframe_interval_s + 1e-9)
        return round(count * self.keyframe_interval_s, 3)

    def next_change_at(self) -> float | None:
        """When `advance` next has something to do (a phase ends, a seek lands, the end)."""
        if self.stuck and self.loading:
            return None
        if self.state == MediaState.OPENING:
            return self.ready_at
        if self.loading:
            return self.first_frame_at
        if self.pending_seek is not None:
            return self.pending_seek[1]
        if self.state == MediaState.PLAYING and self.duration_s is not None and self.speed > 0:
            return self.anchor_time + (self.duration_s - self.anchor_cursor_s) / self.speed
        return None

    def advance(self, now: float) -> str | None:
        """Let time pass; returns "started" or "ended" when that just happened, else None.

        An opening source reports playing once `ready_at` has passed, and
        really starts (from the top) at its first frame. A seek lands when
        due. A playing one that reached its end says "ended" exactly once.
        """
        if self.stuck and self.loading:
            return None
        if self.state == MediaState.OPENING and now >= self.ready_at:
            self._set(MediaState.PLAYING, 0.0, now)
        if self.loading and self.state != MediaState.OPENING and now >= self.first_frame_at:
            self.loading = False
            self._set(self.state, 0.0, now)
            return "started"
        if self.pending_seek is not None and now >= self.pending_seek[1]:
            target, landed_at = self.pending_seek
            self.pending_seek = None
            # The position was `target` from the moment the seek landed.
            self._set(self.state, target, landed_at)
        if self.state != MediaState.PLAYING or self.duration_s is None or self.loading:
            return None
        if self.cursor(now) < self.duration_s:
            return None
        self._set(MediaState.ENDED, self.duration_s, now)
        return "ended"

    def _open(self, now: float, opening_s: float, first_frame_s: float) -> None:
        self.loading, self.pending_seek = True, None
        self.ready_at = now + opening_s
        self.first_frame_at = self.ready_at + first_frame_s
        self._set(MediaState.OPENING, 0.0, now)

    def _set(self, state: str, cursor_s: float, now: float) -> None:
        self.state, self.anchor_cursor_s, self.anchor_time = state, cursor_s, now
