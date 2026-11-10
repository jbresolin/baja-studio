"""Which hosts screens are open, and so which seats are online.

A hosts screen (`/hosts`) says which seats it represents and repeats it
every couple of seconds (`register_screen`, also its way of noticing a dead
link). A seat is online while some screen that represents it has been
heard from within `SCREEN_TIMEOUT_SECONDS`; that is what production's seat
strip shows, since only then can that seat see messages and react.

Written as a plain class told `now` on every call (the conventions'
time-dependent logic); the module's `screens` worker calls `expire` once a
second.
"""

from dataclasses import dataclass

# Screens send every 2 s; three missed in a row means the screen is gone.
SCREEN_TIMEOUT_SECONDS = 6.0


@dataclass
class _Screen:
    seats: tuple[int, ...]
    last_seen: float


class ScreenTracker:
    """Screens by the source that registered them (`web:<client id>`)."""

    def __init__(self) -> None:
        """No screen open yet."""
        self._screens: dict[str, _Screen] = {}

    def heard_from(self, source: str, seats: list[int], now: float) -> bool:
        """A screen (re)registered; returns True if what it represents changed."""
        seats_now = tuple(sorted(set(seats)))
        previous = self._screens.get(source)
        self._screens[source] = _Screen(seats_now, now)
        return previous is None or previous.seats != seats_now

    def expire(self, now: float) -> bool:
        """Forget screens not heard from for too long; returns True if any went."""
        gone = [
            source
            for source, screen in self._screens.items()
            if now - screen.last_seen > SCREEN_TIMEOUT_SECONDS + 1e-6
        ]
        for source in gone:
            del self._screens[source]
        return bool(gone)

    def online_seats(self) -> dict[int, int]:
        """How many open screens represent each seat (seats with none are left out)."""
        counts: dict[int, int] = {}
        for screen in self._screens.values():
            for seat_id in screen.seats:
                counts[seat_id] = counts.get(seat_id, 0) + 1
        return counts

    def screen_count(self) -> int:
        """How many hosts screens are open."""
        return len(self._screens)
