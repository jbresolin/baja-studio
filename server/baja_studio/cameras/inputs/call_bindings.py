"""What the controller's Call buttons do, per the controller's `call_bindings` setting.

Each press is published as `controllers.<id>.last_call` and logged. A bound
button then either:

- `select_camera`: sets the controller's *selected camera*, published as
  `controllers.<id>.selected_camera` ({camera, ts}). It moves nothing; a
  Cameras page with "follow controller" switched on selects the same camera.
- `recall_preset`: recalls a preset on the selected camera, or, before any
  Call has selected one, on the camera the controller last addressed. The
  recall goes through the arbiter as the joystick, so it obeys the holder rule.
"""

from collections.abc import Callable
from typing import Any

from baja_studio.cameras.config import CallAction, CallBinding
from baja_studio.cameras.driver import Logger
from baja_studio.cameras.motion.arbiter import MotionArbiter, MotionRefusedError
from baja_studio.core.clock import Clock


class CallBindings:
    """Carries out Call presses for one controller (on its worker thread)."""

    def __init__(
        self,
        source: str,
        arbiter_for_camera: Callable[[str], MotionArbiter | None],
        publish: Callable[[str, Any], None],
        log_command: Callable[[str, dict[str, Any], str], None],
        clock: Clock,
        logger: Logger,
    ) -> None:
        """`source` is the joystick's command-log source, e.g. "joystick:desk"."""
        self._source = source
        self._arbiter_for_camera = arbiter_for_camera
        self._publish = publish
        self._log_command = log_command
        self._clock = clock
        self._logger = logger
        self._selected: str | None = None
        self._last_addressed: str | None = None

    def addressed(self, camera_id: str) -> None:
        """A frame went to `camera_id`: the preset target until a Call selects a camera."""
        self._last_addressed = camera_id

    def press(self, n: int, binding: CallBinding | None) -> None:
        """Call button `n` was pressed; `binding` is what it is bound to, if anything."""
        ts = self._clock.now_utc().isoformat()
        self._publish("last_call", {"n": n, "ts": ts})
        if binding is None:
            self._log_command("call", {"n": n}, "ok")
        elif binding.action == CallAction.SELECT_CAMERA:
            self._selected = binding.camera
            self._publish("selected_camera", {"camera": binding.camera, "ts": ts})
            self._log_command("call", {"n": n, "select_camera": binding.camera}, "ok")
        elif binding.action == CallAction.RECALL_PRESET:
            assert binding.preset is not None
            self._recall(n, binding.preset)

    def _recall(self, n: int, preset: int) -> None:
        target = self._selected or self._last_addressed
        arbiter = self._arbiter_for_camera(target) if target else None
        args: dict[str, Any] = {"n": n, "camera": target, "preset": preset}
        if arbiter is None:
            reason = f"no enabled camera {target!r}" if target else "no camera selected yet"
            self._logger.warning("Call %d: cannot recall preset %d: %s", n, preset, reason)
            self._log_command("call", args, f"error: {reason}")
            return
        try:
            arbiter.recall_preset(self._source, preset)
        except MotionRefusedError as refused:
            self._log_command("call", args, f"error: {refused}")
            return
        self._log_command("call", args, "ok")
