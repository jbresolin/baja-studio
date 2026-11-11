"""The fake OBS's media timing: delays per input, events sent late, and what program shows.

`FakeObs` keeps one `MediaTimeline`. It hands out the delays of opens and
seeks (zero without a seed, see `obs_timing.py`), holds media events until
they are due, so events of the two playback sources interleave as they do
from real OBS, and watches program: every moment the program scene's media
showed no frame (black on air) is kept for tests to check.
"""

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any

from baja_studio.fakes.obs_timing import ObsTiming


@dataclass(order=True)
class _PendingEvent:
    due: float
    order: int
    event_type: str = field(compare=False)
    event_data: dict[str, Any] = field(compare=False)


class MediaTimeline:
    """Delays, late events and the record of black on program, for one fake OBS."""

    def __init__(self, timing: ObsTiming | None) -> None:
        """`timing` None: no delays, every event sent as it happens."""
        self.timing = timing
        self._pending: list[_PendingEvent] = []
        self._order = itertools.count()
        # One input's events leave OBS in the order they happened.
        self._last_due: dict[str, float] = {}
        # (time, scene) of every look at program that found it black.
        self.black_on_program: list[tuple[float, str]] = []
        # (time, scene) of every program scene change.
        self.program_cuts: list[tuple[float, str]] = []

    def opening_delays(self, input_name: str) -> tuple[float, float]:
        """(opening, first frame) for one open of `input_name`."""
        return self.timing.opening(input_name) if self.timing else (0.0, 0.0)

    def seek_delay(self, input_name: str) -> float:
        """How long a seek on `input_name` takes to land."""
        return self.timing.seek(input_name) if self.timing else 0.0

    def media_event(self, now: float, input_name: str, event_type: str) -> None:
        """A media event happened at `now`; it is sent when due (`due_events`)."""
        delay = self.timing.event(input_name) if self.timing else 0.0
        due = max(now + delay, self._last_due.get(input_name, 0.0))
        self._last_due[input_name] = due
        event = _PendingEvent(due, next(self._order), event_type, {"inputName": input_name})
        heapq.heappush(self._pending, event)

    def due_events(self, now: float) -> list[tuple[str, dict[str, Any]]]:
        """The events to send at `now`, in the order they were due."""
        ready = []
        while self._pending and self._pending[0].due <= now:
            event = heapq.heappop(self._pending)
            ready.append((event.event_type, event.event_data))
        return ready

    def next_event_at(self) -> float | None:
        """When the next held event is due, if any."""
        return self._pending[0].due if self._pending else None

    def look_at_program(self, now: float, scene: str, is_black: bool) -> None:
        """Note what program showed at `now` (kept only when black)."""
        if is_black:
            self.black_on_program.append((now, scene))
