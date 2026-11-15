"""The velocity an OBS crop camera's window is moving at, and its dead-man (told the time).

The arbiter hands the driver a velocity with a time-to-live, renewing it
while a control is held. The driver's worker asks `advance(now)` how far to
move the window since it last asked: the velocity times the time passed,
but never past the moment the velocity expired. A stop simply ends the
integration: there is nothing to send to OBS, the window stays where it is.

A new velocity applies from the worker's last step, not from the exact
moment it arrived; the difference is at most one update (40 ms at 25 Hz).
"""

from dataclasses import dataclass

from baja_studio.cameras.driver import AT_REST, Velocity

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
