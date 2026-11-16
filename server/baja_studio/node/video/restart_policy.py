"""When a pipeline gives up on an ffmpeg run, and how long it waits before the next one.

- **Backoff**: 1 s after the first failure, doubling to 30 s (the
  supervisor's numbers, `core/worker.py`), back to 1 s once a run has
  delivered pictures for `STABLE_RUN_SECONDS`. A camera unplugged for an
  hour costs one attempt every 30 s, never a storm.
- **Watchdog**: ffmpeg can stay alive without making pictures (a camera
  that stops sending, an output that blocks). A run that has produced no
  frame `STARTUP_TIMEOUT_SECONDS` after launch, or whose frame count has not
  moved for `STALL_TIMEOUT_SECONDS`, is stopped and counted as failed.
  Startup is allowed longer: opening a 4K camera, connecting SRT (up to
  3 s) and opening a hardware encoder all happen before the first frame.
"""

from baja_studio.core.worker import FIRST_RESTART_DELAY_SECONDS, MAX_RESTART_DELAY_SECONDS

STABLE_RUN_SECONDS = 10.0
STARTUP_TIMEOUT_SECONDS = 20.0
STALL_TIMEOUT_SECONDS = 5.0


class RestartBackoff:
    """The waits between failed runs: 1, 2, 4, 8, 16, 30, 30… seconds until `reset`."""

    def __init__(self) -> None:
        """No failure yet: the first wait is the shortest."""
        self._next = FIRST_RESTART_DELAY_SECONDS

    def next_delay(self) -> float:
        """The wait before the next run; each call doubles the one after (up to the maximum)."""
        delay = self._next
        self._next = min(self._next * 2, MAX_RESTART_DELAY_SECONDS)
        return delay

    def reset(self) -> None:
        """A run went well: the next failure waits the shortest time again."""
        self._next = FIRST_RESTART_DELAY_SECONDS


def stall_reason(now: float, launched_at: float, frames_changed_at: float | None) -> str | None:
    """Why a still-running ffmpeg should be stopped, or None while it is fine.

    `frames_changed_at` is when its output frame count last went up (None
    before the first frame); all three are clock times in seconds.
    """
    if frames_changed_at is None:
        if now - launched_at >= STARTUP_TIMEOUT_SECONDS:
            return f"no picture {STARTUP_TIMEOUT_SECONDS:.0f} s after starting"
        return None
    if now - frames_changed_at >= STALL_TIMEOUT_SECONDS:
        return f"the picture stopped for {STALL_TIMEOUT_SECONDS:.0f} s"
    return None
