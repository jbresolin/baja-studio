"""Two-operator safety: with confirmation on, a take needs a second press within 3 s.

The first take arms (and every page shows "Press Take again"); a second take
from any client inside the window fires. The window closes by itself after
3 s. Called with the playout controller's lock held; the expiry takes it.
"""

import threading

from baja_studio.core.clock import Clock
from baja_studio.modules.playout.publisher import PlayoutPublisher

TAKE_CONFIRMATION_WINDOW_SECONDS = 3.0


class TakeConfirmation:
    """Whether a take is armed, until when, and telling every page."""

    def __init__(self, clock: Clock, publisher: PlayoutPublisher, lock: threading.RLock) -> None:
        """`lock` is the controller's; the expiry timer takes it."""
        self._clock = clock
        self._publisher = publisher
        self._lock = lock
        self._armed_until: float | None = None

    def is_armed(self) -> bool:
        """True inside the window after a first press."""
        armed_until = self._armed_until
        return armed_until is not None and self._clock.monotonic() <= armed_until

    def arm(self, source: str) -> None:
        """A first press from `source`: open the window and show the prompt."""
        armed_until = self._clock.monotonic() + TAKE_CONFIRMATION_WINDOW_SECONDS
        self._armed_until = armed_until
        self._publisher.publish_take_confirmation(
            {"requested_by": source, "window_s": TAKE_CONFIRMATION_WINDOW_SECONDS}
        )
        self._clock.call_later(TAKE_CONFIRMATION_WINDOW_SECONDS, lambda: self._expire(armed_until))

    def disarm(self) -> None:
        """The take fired (or the window closed): clear the prompt."""
        self._armed_until = None
        self._publisher.publish_take_confirmation(None)

    def _expire(self, armed_until: float) -> None:
        with self._lock:
            if self._armed_until == armed_until:
                self.disarm()
