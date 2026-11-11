"""Seeded random timing for the fake OBS: how long its media sources take.

Real OBS opens files, seeks and sends events on its own threads, so how
long each takes varies from one try to the next (the WP5c bench: a live
speed change sometimes worked, sometimes went black or restarted from 0).
`ObsTiming` draws those delays from a seed, so a race found with one seed
comes back every time with that seed. Each input has its own random stream,
so requests from another thread (playout's 5 Hz poll) never shift the
delays one source gets.

No timing (`FakeObs(timing=None)`) means every delay is zero: a file starts
on the next tick and seeks land at once, as the fake always did.
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class TimingRanges:
    """Lowest and highest delay, in seconds, of each thing OBS does in the background."""

    # A file opened: status says "opening" for this long, then "playing" without a frame.
    opening_s: tuple[float, float] = (0.0, 0.03)
    # ...and the first frame (and MediaInputPlaybackStarted) comes this much later.
    first_frame_s: tuple[float, float] = (0.05, 0.5)
    # A seek on a source with a frame lands this much later.
    seek_s: tuple[float, float] = (0.0, 0.1)
    # A media event leaves OBS this much after it happened.
    event_s: tuple[float, float] = (0.0, 0.15)


class ObsTiming:
    """Delays for each input's opens, seeks and events, drawn from one seed."""

    def __init__(self, seed: int, ranges: TimingRanges | None = None) -> None:
        """The same seed gives the same delays, input by input, in the same order."""
        self.seed = seed
        self.ranges = ranges or TimingRanges()
        self._streams: dict[str, random.Random] = {}

    def opening(self, input_name: str) -> tuple[float, float]:
        """(opening, first frame) delays for one open of `input_name`."""
        return (
            self._draw(input_name, self.ranges.opening_s),
            self._draw(input_name, self.ranges.first_frame_s),
        )

    def seek(self, input_name: str) -> float:
        """How long a seek on `input_name` takes to land."""
        return self._draw(input_name, self.ranges.seek_s)

    def event(self, input_name: str) -> float:
        """How long after it happened an event of `input_name` is sent."""
        return self._draw(input_name, self.ranges.event_s)

    def _draw(self, input_name: str, bounds: tuple[float, float]) -> float:
        stream = self._streams.get(input_name)
        if stream is None:
            stream = self._streams[input_name] = random.Random(f"{self.seed}:{input_name}")
        low, high = bounds
        # Whole milliseconds, like OBS's cursor, so positions compare exactly.
        return round(stream.uniform(low, high), 3)
