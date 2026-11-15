"""The velocity an OBS crop camera's window is moving at, and its dead-man (told the time).

`CropCommands` wraps it, with a pending jump (preset recall, click-to-centre),
behind one lock: the arbiter's thread writes, the driver's worker takes.

The arbiter hands the driver a velocity with a time-to-live, renewing it
while a control is held. The driver's worker asks `advance(now)` how far to
move the window since it last asked: the velocity times the time passed,
but never past the moment the velocity expired. A stop simply ends the
integration: there is nothing to send to OBS, the window stays where it is.

A new velocity applies from the worker's last step, not from the exact
moment it arrived; the difference is at most one update (40 ms at 25 Hz).
"""

import threading
from dataclasses import dataclass

from baja_studio.cameras.crop_window import WindowTarget
from baja_studio.cameras.driver import AT_REST, Velocity
from baja_studio.core.clock import Clock

# Deadlines are float sums; ten 0.1 s steps fall short of 1.0 without slack.
TIME_TOLERANCE_SECONDS = 1e-6


@dataclass(frozen=True)
class MotionStep:
    """How to move the window this step: at `velocity` for `seconds`."""

    velocity: Velocity
    seconds: float


class CropMotion:
    """One camera's held velocity; not thread-safe (the driver holds its lock)."""

    def __init__(self) -> None:
        """At rest."""
        self._velocity = AT_REST
        self._expires_at = 0.0
        self._last_step_at: float | None = None

    def move(self, velocity: Velocity, ttl_seconds: float, now: float) -> None:
        """Hold `velocity` until `now + ttl_seconds`, unless renewed or stopped."""
        if not self.moving:
            # Starting from rest: time before now was spent standing still.
            self._last_step_at = now
        self._velocity = velocity
        self._expires_at = now + ttl_seconds

    def stop(self) -> None:
        """Stop integrating (the window stays where it is)."""
        self._velocity = AT_REST

    def pause(self, now: float) -> None:
        """The worker could not move the window just now (it is measuring the source):
        the time until `now` is not caught up later."""
        self._last_step_at = now

    @property
    def moving(self) -> bool:
        """True while a non-zero velocity is held (it may be about to expire)."""
        return not self._velocity.is_zero

    def advance(self, now: float) -> MotionStep:
        """How far to move since the last step; the velocity ends once it expired."""
        last, self._last_step_at = self._last_step_at, now
        if not self.moving or last is None:
            return MotionStep(AT_REST, 0.0)
        end = min(now, self._expires_at)
        step = MotionStep(self._velocity, max(0.0, end - last))
        if now >= self._expires_at - TIME_TOLERANCE_SECONDS:
            self._velocity = AT_REST
        return step


Jump = WindowTarget | int  # a window target, or a stored preset's number


class CropCommands:
    """What the arbiter asked for, kept for the worker: a held velocity and a pending jump.

    The arbiter's thread writes, the driver's worker takes; one lock guards both.
    """

    def __init__(self, clock: Clock) -> None:
        """At rest, no jump."""
        self._clock = clock
        self._lock = threading.Lock()
        self._motion = CropMotion()
        self._jump: Jump | None = None

    def move(self, velocity: Velocity, ttl_seconds: float) -> None:
        """Hold a velocity (a new one replaces the old)."""
        with self._lock:
            self._motion.move(velocity, ttl_seconds, self._clock.monotonic())

    def stop(self) -> None:
        """Stop moving (a pending jump stays)."""
        with self._lock:
            self._motion.stop()

    def jump(self, target: Jump) -> None:
        """Jump the window; any velocity stops."""
        with self._lock:
            self._motion.stop()
            self._jump = target

    def forget(self) -> None:
        """OBS cannot be told anything: drop the velocity and the jump."""
        with self._lock:
            self._motion.stop()
            self._jump = None

    def take(self, now: float) -> tuple[Jump | None, MotionStep]:
        """The worker's step: the pending jump (taken), and how far to move."""
        with self._lock:
            jump, self._jump = self._jump, None
            return jump, self._motion.advance(now)

    def take_reset(self) -> bool:
        """True (once) if "show the whole picture" is pending: it needs no size to apply."""
        with self._lock:
            if isinstance(self._jump, WindowTarget) and self._jump.whole:
                self._jump = None
                return True
            return False

    def pause(self, now: float) -> None:
        """The worker cannot move the window now; this time is not caught up later."""
        with self._lock:
            self._motion.pause(now)

    @property
    def moving(self) -> bool:
        """A velocity is held."""
        with self._lock:
            return self._motion.moving

    @property
    def at_rest(self) -> bool:
        """No velocity and no jump waiting."""
        with self._lock:
            return not self._motion.moving and self._jump is None
