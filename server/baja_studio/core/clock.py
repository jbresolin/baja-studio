"""Time source used by every core component.

All waiting and timing goes through a `Clock` so tests can swap in a fake
clock and control time explicitly instead of sleeping.
"""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol


class TimerHandle(Protocol):
    """Something returned by `Clock.call_later` that can be cancelled."""

    def cancel(self) -> None:
        """Prevent the callback from running if it has not run yet."""


class Clock(Protocol):
    """The operations core code needs from time."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary start; only differences are meaningful."""

    def now_utc(self) -> datetime:
        """Current wall-clock time in UTC, used for timestamps in logs and the DB."""

    def wait(self, event: threading.Event, seconds: float) -> bool:
        """Block until `event` is set or `seconds` pass; return True if the event was set."""

    def call_later(self, seconds: float, callback: Callable[[], None]) -> TimerHandle:
        """Run `callback` once after `seconds`, on some other thread."""


class SystemClock:
    """The real clock used when the service runs."""

    def monotonic(self) -> float:
        """Seconds from the OS monotonic clock."""
        return time.monotonic()

    def now_utc(self) -> datetime:
        """Current UTC time from the OS."""
        return datetime.now(UTC)

    def wait(self, event: threading.Event, seconds: float) -> bool:
        """Wait on the event with a real timeout."""
        return event.wait(seconds)

    def call_later(self, seconds: float, callback: Callable[[], None]) -> TimerHandle:
        """Start a daemon `threading.Timer`; daemon so it never blocks shutdown."""
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        timer.start()
        return timer
