"""The motion arbiter: one per camera, the only thing that calls the camera's driver.

Inputs (the joystick, the web page, the HTTP API) are *sources*, named like
the command log names them: `joystick:<controller id>`, `web:<client>`,
`api:<caller>`. The rules:

- One source at a time holds the camera's velocity (it *owns* the camera).
  Another source takes over only once the owner is at rest (velocity zero)
  or its velocity has expired.
- A velocity lasts `ttl_ms` (the source's own, or the motion setting) unless
  renewed; when it runs out the arbiter stops the camera: the dead-man switch.
- A *latched* velocity (the joystick's) does not expire: Pelco-D says what the
  stick does, not for how long, so the velocity holds until the controller
  sends something else. The joystick worker releases the latch on a stop
  frame, a different frame, a change of camera, or a lost serial link.
- While a velocity is held it is passed to the driver again every
  `resend_interval_ms` (with the time it has left, or a full `ttl_ms` when
  latched), so drivers keep their own dead-man satisfied, and a driver whose
  route changed (agent → direct fallback) is told at once.
- A stop from the web page or the API is always executed. A stop on behalf of
  the joystick (`only_if_owner`) applies only when the joystick owns the
  camera: a controller at rest may repeat "stop", and on an agent-owned camera
  every stop would pause auto tracking (contract §7), or cut into another
  source's move.
"""

import math
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from baja_studio.agents.contract import MAX_TTL_MS, Mode, ModeReply
from baja_studio.cameras.config import CameraConfig, MotionSettings
from baja_studio.cameras.driver import CameraDriver, Logger, ModeCapableDriver, Velocity
from baja_studio.cameras.motion.shaping import shape
from baja_studio.core.clock import Clock

JOYSTICK_SOURCE_PREFIX = "joystick:"
# Longest the motion worker sleeps when nothing is held (it is woken by every command).
IDLE_TICK_SECONDS = 1.0
# Deadlines are float sums of intervals; without slack, ten 0.1 s steps fall short of 1.0.
TIME_TOLERANCE_SECONDS = 1e-6


class MotionRefusedError(Exception):
    """The command cannot be carried out now; the message says why, for the operator."""


@dataclass
class _Hold:
    source: str
    velocity: Velocity
    expires_at: float  # math.inf when latched
    last_sent_at: float

    @property
    def latched(self) -> bool:
        return math.isinf(self.expires_at)


class MotionArbiter:
    """Decides, for one camera, whose commands reach the driver."""

    def __init__(
        self,
        driver: CameraDriver,
        config: Callable[[], CameraConfig],
        motion: Callable[[], MotionSettings],
        clock: Clock,
        publish: Callable[[str, Any], None],
        logger: Logger,
        on_activity: Callable[[], None] = lambda: None,
    ) -> None:
        """`config` and `motion` are read on every command, so edits apply at once.

        `on_activity` is called after a new velocity is held, so the motion
        worker (which may be sleeping for a second) re-plans its next tick.
        """
        self._driver = driver
        self._config = config
        self._motion = motion
        self._clock = clock
        self._publish = publish
        self._logger = logger
        self._lock = threading.Lock()
        self._hold: _Hold | None = None
        self._route = driver.route
        self._on_activity = on_activity

    @property
    def camera_id(self) -> str:
        """The camera this arbiter drives."""
        return self._config().id

    @property
    def driver(self) -> CameraDriver:
        """The driver this arbiter commands."""
        return self._driver

    # --- commands from sources --------------------------------------------------------

    def move(
        self, source: str, velocity: Velocity, ttl_ms: int | None = None, latched: bool = False
    ) -> dict[str, Any]:
        """Drive at `velocity` (normalised, before shaping) on behalf of `source`.

        A velocity that shapes to zero means "this source is at rest": it ends
        the source's own hold (a stop) and is otherwise ignored, so a stick
        at rest sends nothing. `latched`: hold it until this source says
        otherwise, with no ttl. Raises `MotionRefusedError` if another source
        holds the camera.
        """
        motion = self._motion()
        config = self._config()
        deadband = motion.joystick_deadband if source.startswith(JOYSTICK_SOURCE_PREFIX) else 0.0
        shaped = shape(velocity, config.limits, config.speed_curve, deadband)
        ttl_seconds = max(1, min(MAX_TTL_MS, ttl_ms or motion.ttl_ms)) / 1000
        with self._lock:
            now = self._clock.monotonic()
            self._refuse_if_held_by_another(source, now)
            hold = self._active_hold(now)
            if shaped.is_zero:
                if hold is not None and hold.source == source:
                    self._stop_locked(source, "released")
                return {"moving": False, "started": False}
            started = hold is None or hold.source != source
            changed = started or hold is None or hold.velocity != shaped
            self._hold = _Hold(
                source,
                shaped,
                expires_at=math.inf if latched else now + ttl_seconds,
                last_sent_at=now if changed or hold is None else hold.last_sent_at,
            )
            if changed:
                self._driver.move(shaped, round(ttl_seconds * 1000))
                self._publish_control(source, "move")
        if changed:
            self._on_activity()
        return {"moving": True, "started": started, "velocity": _velocity_dict(shaped)}

    def stop(self, source: str, only_if_owner: bool = False, reason: str = "stop") -> bool:
        """Stop every axis now; returns whether it did.

        `only_if_owner`: only if `source` holds the velocity (the joystick's
        stops); otherwise always (the web page's and the API's).
        """
        with self._lock:
            if only_if_owner:
                hold = self._active_hold(self._clock.monotonic())
                if hold is None or hold.source != source:
                    return False
            self._stop_locked(source, reason)
        return True

    def recall_preset(self, source: str, n: int) -> dict[str, Any]:
        """Go to preset `n`; it ends any velocity, like a stop, so it obeys the same holder rule."""
        self._check_preset_number(n)
        with self._lock:
            self._refuse_if_held_by_another(source, self._clock.monotonic())
            self._hold = None
            self._driver.recall_preset(n)
            self._publish_control(source, f"recall_preset {n}")
        return {"recalled": n}

    def store_preset(self, source: str, n: int) -> None:
        """Store the current position as preset `n` (no motion, so any source may)."""
        self._check_preset_number(n)
        with self._lock:
            self._driver.store_preset(n)
            self._publish_control(source, f"store_preset {n}")

    def set_mode(
        self, source: str, mode: Mode, profile: dict[str, Any] | None
    ) -> "Future[ModeReply]":
        """Set the agent's mode for an agent-owned camera; refused for direct cameras."""
        if not isinstance(self._driver, ModeCapableDriver):
            raise MotionRefusedError("only agent-owned cameras have modes (manual, auto, hold)")
        with self._lock:
            self._publish_control(source, f"set_mode {mode.value}")
            return self._driver.set_mode(mode, profile)

    # --- the motion worker's tick -------------------------------------------------------

    def tick(self) -> float:
        """Expire, re-send or re-route the held velocity; return seconds until the next tick."""
        with self._lock:
            now = self._clock.monotonic()
            hold = self._hold
            route = self._driver.route
            rerouted, self._route = route != self._route, route
            if hold is None or hold.velocity.is_zero:
                self._hold = None
                return IDLE_TICK_SECONDS
            if now >= hold.expires_at:
                self._logger.info(
                    "camera %s: velocity from %s expired; stopping", self._config().id, hold.source
                )
                self._stop_locked(hold.source, "expired")
                return IDLE_TICK_SECONDS
            motion = self._motion()
            resend_interval = motion.resend_interval_ms / 1000
            if rerouted or now - hold.last_sent_at >= resend_interval - TIME_TOLERANCE_SECONDS:
                if hold.latched:
                    remaining_ms = motion.ttl_ms
                else:
                    remaining_ms = max(1, round((hold.expires_at - now) * 1000))
                self._driver.move(hold.velocity, remaining_ms)
                hold.last_sent_at = now
            next_due = min(hold.last_sent_at + resend_interval, hold.expires_at)
            return max(0.0, next_due - now)

    # --- inside the lock ------------------------------------------------------------

    def _active_hold(self, now: float) -> _Hold | None:
        hold = self._hold
        if hold is None or hold.velocity.is_zero or now >= hold.expires_at:
            return None
        return hold

    def _refuse_if_held_by_another(self, source: str, now: float) -> None:
        hold = self._active_hold(now)
        if hold is not None and hold.source != source:
            raise MotionRefusedError(
                f"camera {self._config().id} is being moved by {hold.source}; "
                "it takes over once that stops"
            )

    def _stop_locked(self, source: str, reason: str) -> None:
        self._hold = None
        self._driver.stop()
        self._publish_control(source, reason)

    def _publish_control(self, source: str, command: str) -> None:
        hold = self._hold
        self._publish(
            "control",
            {
                "velocity_owner": hold.source if hold else None,
                "velocity": _velocity_dict(hold.velocity) if hold else None,
                "latched": hold.latched if hold else False,
                "last_source": source,
                "last_command": command,
                "last_command_ts": self._clock.now_utc().isoformat(),
            },
        )

    def _check_preset_number(self, n: int) -> None:
        preset_max = self._driver.capabilities().preset_max
        if preset_max is None:
            raise MotionRefusedError(f"camera {self._config().id} has no presets")
        if not 0 <= n <= preset_max:
            raise MotionRefusedError(f"preset numbers on this camera go from 0 to {preset_max}")


def _velocity_dict(velocity: Velocity) -> dict[str, float]:
    return {"pan": velocity.pan, "tilt": velocity.tilt, "zoom": velocity.zoom}
