"""When an OBS crop camera measures its source, and which size it trusts (told the time).

Measuring takes a full-size screenshot (`obs_crop_sync.measure_source`), so
it is done only when something says the size may have changed:

- the size is not known (start, OBS back, a new filter): motion waits for it;
- the camera's settings changed (`InputSettingsChanged`). If they name a
  `resolution` ("1920x1080"), that size is trusted at once (the crop is
  re-expressed for it before the device has even reopened, so a crop larger
  than the new picture does not leave OBS showing one pixel) and confirmed by
  measuring until the screenshot agrees, for up to `RESOLUTION_WAIT_SECONDS`:
  a capture device reopens on its own thread, so the first screenshots can
  still have the old size. Without a resolution, it is measured at once and
  again `RECHECKS_AFTER_CHANGE` later;
- every `REMEASURE_AT_REST_SECONDS` at rest, in case a change came unannounced.

A measurement waits until neither the studio nor anyone else changed the
filter for `QUIET_BEFORE_MEASURING_SECONDS`: OBS applies new edges at its
next video frame, and a screenshot before that has the old size.
"""

import math
from enum import Enum

from baja_studio.cameras.drivers.obs_crop_sync import QUIET_BEFORE_MEASURING_SECONDS

RESOLUTION_WAIT_SECONDS = 5.0
RECHECKS_AFTER_CHANGE = (1.0, 3.0)
REMEASURE_AT_REST_SECONDS = 30.0
Size = tuple[int, int]


class Measured(Enum):
    """What a measurement did."""

    SAME = "same"
    CHANGED = "changed"
    # It disagrees with the announced resolution: the device is still reopening.
    NOT_YET = "not_yet"


class SourceSize:
    """One camera's source size and its measuring schedule; not thread-safe (the worker's)."""

    def __init__(self) -> None:
        """Size unknown: the first measurement is needed before anything moves."""
        self.size: Size | None = None
        self.needed = True
        self._announced: Size | None = None
        self._announced_until = -math.inf
        self._rechecks: list[float] = []
        self._measured_at = -math.inf
        self._touched_at = -math.inf

    def touched(self, now: float) -> None:
        """The filter's edges changed (written by us or edited in OBS)."""
        self._touched_at = now

    def unknown(self) -> None:
        """Measure again before moving (OBS came back, the filter was replaced)."""
        self.needed = True

    def settings_changed(self, resolution: Size | None, now: float) -> bool:
        """`InputSettingsChanged`; True if the trusted size changed (re-express the crop)."""
        self.needed = True
        if resolution is None:
            self._rechecks = [now + delay for delay in RECHECKS_AFTER_CHANGE]
            return False
        self._announced, self._announced_until = resolution, now + RESOLUTION_WAIT_SECONDS
        changed = self.size is not None and resolution != self.size
        self.size = resolution
        return changed

    def quiet(self, now: float) -> bool:
        """True once the filter has not changed for long enough to measure."""
        return now - self._touched_at >= QUIET_BEFORE_MEASURING_SECONDS - 1e-9

    def due(self, now: float, at_rest: bool) -> bool:
        """Whether to measure now (always False until the filter is quiet)."""
        if not self.quiet(now):
            return False
        if self.needed:
            return True
        if not at_rest:
            return False
        if self._rechecks and now >= self._rechecks[0] - 1e-9:
            return True
        return now - self._measured_at >= REMEASURE_AT_REST_SECONDS - 1e-9

    def waiting_for_announced(self, now: float) -> bool:
        """True while an announced resolution may still appear (no picture yet is no news)."""
        return self._announced is not None and now < self._announced_until

    def measured(self, size: Size, now: float) -> Measured:
        """Record a measurement."""
        self._measured_at = now
        self._rechecks = [moment for moment in self._rechecks if moment > now + 1e-9]
        unconfirmed = self._announced is not None and size != self._announced
        if unconfirmed and now < self._announced_until:
            return Measured.NOT_YET
        self._announced, self.needed = None, False
        changed = self.size is not None and size != self.size
        self.size = size
        return Measured.CHANGED if changed else Measured.SAME
