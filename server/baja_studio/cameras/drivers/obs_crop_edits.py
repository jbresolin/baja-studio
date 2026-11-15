"""Telling an OBS crop camera's own writes from edits made in OBS (told the time).

Every `SetSourceFilterSettings` comes back as `SourceFilterSettingsChanged`
(obs-websocket 5.4+), and so does an operator's edit in OBS's filter dialog
(and the dialog's Cancel, which puts the old values back). The rule
(conventions: write protection):

- an event equal to what the studio last wrote changes nothing (an echo, or
  a Cancel back to our values), whenever it comes;
- any other event within `WRITE_PROTECTION_SECONDS` of a write is not
  trusted: a late echo of an earlier write looks like that. But it may be a
  real edit, so the filter is read again once the window has passed;
- any other event after the window is an edit made in OBS: read the filter
  and take its crop as the window.

While the window moves, the studio keeps writing, so an edit in OBS made
then is overwritten by the next step: the stick wins.
"""

import math

from baja_studio.cameras.crop_window import PixelCrop

WRITE_PROTECTION_SECONDS = 0.5


class EditWatch:
    """What the studio last wrote, and whether OBS's filter must be read again."""

    def __init__(self) -> None:
        """Nothing written yet."""
        self.written: PixelCrop | None = None
        self.written_at = -math.inf
        self._read_again_at: float | None = None

    def wrote(self, crop: PixelCrop, now: float) -> None:
        """The studio sent these edges."""
        self.written, self.written_at = crop, now

    def settings_event(self, crop: PixelCrop, now: float) -> bool:
        """OBS says the filter now has `crop`; True to read the filter again now."""
        if crop == self.written:
            return False
        if now - self.written_at < WRITE_PROTECTION_SECONDS:
            self._read_again_at = self.written_at + WRITE_PROTECTION_SECONDS
            return False
        return True

    def read_again_due(self, now: float) -> bool:
        """True once a doubtful event's window has passed (then forgotten)."""
        if self._read_again_at is None or now < self._read_again_at - 1e-9:
            return False
        self._read_again_at = None
        return True

    def read_again_at(self) -> float | None:
        """When a doubtful event will be checked (the worker wakes then)."""
        return self._read_again_at
