"""One live speed change at a time; a click meanwhile replaces the waiting wish.

A swap takes OBS up to 2 s (`playout/live_speed.py`). Rapid clicks on the
speed buttons (a double click, two operators) must not start changes that
fight over the off-air source, and must not each wait their turn either:
while a change runs, a new wish replaces the one waiting, so when the
running change ends only the *last* wish is carried out (if it differs from
the speed then on air). The caller who started the run carries the waiting
wishes out on its own thread; later callers return at once ("queued").

State `speed_change` = {changing, target, error}: the page shows
"changing…" and disables the speed control while `changing`, and shows
`error` (the last change that failed; cleared when the next one starts).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# (speed_percent, source of the command)
Wish = tuple[int, str]


def initial_speed_change_state() -> dict[str, Any]:
    """No change running, none failed."""
    return {"changing": False, "target": None, "error": None}


@dataclass
class _Run:
    target: int | None = None
    error: str | None = None


class SpeedChangeQueue:
    """Serialises `set_program_speed`: one running, at most one waiting (the last)."""

    def __init__(
        self, change: Callable[[int, str], None], publish: Callable[[dict[str, Any]], None]
    ) -> None:
        """`change(speed, source)` makes one change (taking playout's lock); `publish` the state."""
        self._change = change
        self._publish = publish
        self._lock = threading.Lock()
        self._running = False
        self._waiting: Wish | None = None
        self._run = _Run()

    def request(self, speed_percent: int, source: str) -> dict[str, Any]:
        """Ask for a speed; runs it (and whatever is wished meanwhile) unless a change is running.

        Returns `{status: "queued" | "done", speed_percent}` (done: the
        speed last carried out). Raises the error of the last change this
        call carried out, if it failed (`SwitchError`, or OBS's).
        """
        with self._lock:
            self._waiting = (speed_percent, source)
            if self._running:
                self._run.target = speed_percent
                self._publish_locked()
                return {"status": "queued", "speed_percent": speed_percent}
            self._running = True
        failure: Exception | None = None
        carried_out = speed_percent
        try:
            while True:
                with self._lock:
                    wish, self._waiting = self._waiting, None
                    if wish is None:
                        break
                    self._run = _Run(target=wish[0])
                    self._publish_locked()
                carried_out = wish[0]
                try:
                    self._change(*wish)
                    failure = None
                except Exception as error:
                    # Kept for the page; the next wish (if any) is still carried out.
                    failure = error
                    with self._lock:
                        self._run.error = str(error)
        finally:
            with self._lock:
                self._running = False
                self._run.target = None
                self._publish_locked()
        if failure is not None:
            raise failure
        return {"status": "done", "speed_percent": carried_out}

    def _publish_locked(self) -> None:
        self._publish(
            {"changing": self._running, "target": self._run.target, "error": self._run.error}
        )
