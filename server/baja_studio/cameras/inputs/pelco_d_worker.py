"""The joystick controller's worker: reads the serial port and routes what it hears.

One worker per enabled controller. It opens the port (pyserial, 100 ms read
timeout), feeds the bytes to the Pelco-D parser, and turns each frame into an
arbiter call for the camera whose `controller_address` matches the frame's.

- A port that is missing or unplugged is `offline`; the worker tries again
  every second by itself, so replugging the adapter needs no restart.
- A move frame *latches* its velocity on the camera (see the arbiter): it
  holds, re-sent by the arbiter, until a stop frame, a different frame, or a
  frame for another camera releases it. So a stick held perfectly still never
  times out, whether or not the controller repeats frames.
- Identical consecutive frames (a controller that repeats while a stick is
  held) change nothing.
- Stop frames are never deduplicated: each one is a full stop (pan, tilt and
  zoom), but only of a camera the joystick owns; a stop frame does not cut
  into a move from the web page, and does not pause an agent's auto mode.
- Losing the link releases the latch: a port error, the worker stopping, or
  (if `motion.controller_silence_ms` is set) no frame for that long.
- Call-button presses are published as `controllers.<id>.last_call`.
"""

from collections.abc import Callable
from typing import Any, Protocol

import serial

from baja_studio.cameras.config import ControllerConfig, MotionSettings
from baja_studio.cameras.driver import Logger, Velocity
from baja_studio.cameras.inputs.pelco_d import (
    CallButton,
    Frame,
    FrameParser,
    Motion,
    RecallPreset,
    StopAll,
    Unknown,
    interpret,
)
from baja_studio.cameras.motion.arbiter import MotionArbiter, MotionRefusedError
from baja_studio.core.clock import Clock
from baja_studio.core.supervisor import HealthState, WorkerContext, WorkerSpec

READ_TIMEOUT_SECONDS = 0.1
READ_SIZE_BYTES = 32
REOPEN_INTERVAL_SECONDS = 1.0
REPEATED_WARNING_INTERVAL_SECONDS = 60.0


class SerialPort(Protocol):
    """The part of `serial.Serial` the worker uses."""

    def read(self, size: int) -> bytes:
        """Up to `size` bytes, waiting at most the port's timeout."""

    def close(self) -> None:
        """Release the port."""


SerialOpener = Callable[[str, int], SerialPort]


def open_serial_port(port: str, baud: int) -> SerialPort:
    """The real port: 8N1 at `baud`, reads time out after 100 ms."""
    return serial.Serial(port, baud, timeout=READ_TIMEOUT_SECONDS)


class PelcoDInput:
    """One RS485 controller and the routing of its frames to cameras."""

    def __init__(
        self,
        config: ControllerConfig,
        arbiter_for_address: Callable[[int], MotionArbiter | None],
        motion: Callable[[], MotionSettings],
        publish: Callable[[str, Any], None],
        log_command: Callable[[str, dict[str, Any], str], None],
        clock: Clock,
        logger: Logger,
        opener: SerialOpener = open_serial_port,
    ) -> None:
        """`publish(key, value)` writes `controllers.<id>.<key>`; `log_command` the command log."""
        self.config = config
        self.source = f"joystick:{config.id}"
        self._arbiter_for_address = arbiter_for_address
        self._motion = motion
        self._publish = publish
        self._log_command = log_command
        self._clock = clock
        self._logger = logger
        self._opener = opener
        self._parser = FrameParser()
        self._last_frame: Frame | None = None
        self._last_frame_at = clock.monotonic()
        # The camera the joystick latched a velocity on, if any (worker thread only).
        self._latched: MotionArbiter | None = None
        self._warned_at: dict[str, float] = {}

    @property
    def worker_name(self) -> str:
        """The supervisor's name for this controller's worker."""
        return f"controller:{self.config.id}"

    def worker_spec(self) -> WorkerSpec:
        """The worker to start for this controller."""
        return WorkerSpec(self.worker_name, self.run, starts_offline=True)

    # --- the worker -------------------------------------------------------------------

    def run(self, worker: WorkerContext) -> None:
        """Open, read, route; on any port error report offline and reopen a second later."""
        self._set_status(worker, HealthState.OFFLINE, "opening the port")
        try:
            self._open_and_read(worker)
        finally:
            # Removed from the settings or shutting down: the stick no longer drives anything.
            self.release_latch("controller stopped")

    def _open_and_read(self, worker: WorkerContext) -> None:
        while not worker.should_stop:
            try:
                port = self._opener(self.config.port, self.config.baud)
            except (serial.SerialException, OSError, ValueError) as error:
                self._set_status(
                    worker, HealthState.OFFLINE, f"cannot open {self.config.port}: {error}"
                )
                worker.sleep(REOPEN_INTERVAL_SECONDS)
                continue
            self._set_status(worker, HealthState.OK, "")
            self._last_frame_at = self._clock.monotonic()
            try:
                self._read_until_stopped(worker, port)
            except (serial.SerialException, OSError) as error:
                self.release_latch("serial link lost")
                self._set_status(worker, HealthState.OFFLINE, f"{self.config.port} lost: {error}")
                worker.sleep(REOPEN_INTERVAL_SECONDS)
            finally:
                port.close()

    def _read_until_stopped(self, worker: WorkerContext, port: SerialPort) -> None:
        while not worker.should_stop:
            data = port.read(READ_SIZE_BYTES)
            if data:
                for frame in self._parser.feed(data):
                    self.handle(frame)
            self.check_silence()

    def check_silence(self) -> None:
        """Release the latch if the controller has been silent for `controller_silence_ms`."""
        silence_ms = self._motion().controller_silence_ms
        if self._latched is None or silence_ms <= 0:
            return
        if self._clock.monotonic() - self._last_frame_at >= silence_ms / 1000:
            self._logger.warning(
                "controller %s silent for %d ms; stopping its camera", self.config.id, silence_ms
            )
            self.release_latch("controller silent")

    def _set_status(self, worker: WorkerContext, state: HealthState, reason: str) -> None:
        worker.report_health(state, reason)
        self._publish("status", state.value)
        self._publish("status_reason", reason)

    # --- frames ------------------------------------------------------------------------

    def handle(self, frame: Frame) -> None:
        """Carry out one valid frame (called by the worker; tests call it directly)."""
        action = interpret(frame)
        repeated = frame == self._last_frame
        self._last_frame = frame
        self._last_frame_at = self._clock.monotonic()
        arbiter = (
            self._arbiter_for_address(action.address)
            if isinstance(action, StopAll | Motion | RecallPreset)
            else None  # the Call button and unknown frames are for no camera
        )
        if self._latched is not None and (
            self._latched is not arbiter or not isinstance(action, StopAll | Motion)
        ):
            # A different frame, or a frame for another camera: the latch ends here.
            self._release("released")
        if isinstance(action, CallButton):
            if not repeated:
                self._publish("last_call", {"n": action.n, "ts": self._clock.now_utc().isoformat()})
                self._log_command("call", {"n": action.n}, "ok")
            return
        if isinstance(action, Unknown):
            self._warn_now_and_then(f"frame:{frame.command2}", "unknown frame %s", frame)
            return
        if arbiter is None:
            self._warn_now_and_then(
                f"address:{action.address}",
                "controller %s: no camera answers to address %d",
                self.config.id,
                action.address,
            )
            return
        if isinstance(action, StopAll):
            self._latched = None
            if arbiter.stop(self.source, only_if_owner=True):
                self._log_command("stop", {"camera": arbiter.camera_id}, "ok")
        elif repeated:
            pass  # a held stick or button: the latch, or the recall, already does it
        elif isinstance(action, Motion):
            self._latch(arbiter, action)
        elif isinstance(action, RecallPreset):
            preset = action.n
            self._carry_out(
                arbiter,
                "recall_preset",
                {"n": preset},
                lambda: arbiter.recall_preset(self.source, preset),
            )

    def release_latch(self, reason: str) -> None:
        """The link is gone: stop the camera the joystick latched, if it still owns it.

        Also forgets the last frame, so the next one is acted on even if it
        is the same (the operator still holding the stick after a glitch).
        """
        self._last_frame = None
        self._release(reason)

    def _release(self, reason: str) -> None:
        arbiter, self._latched = self._latched, None
        if arbiter is not None and arbiter.stop(self.source, only_if_owner=True, reason=reason):
            self._log_command("stop", {"camera": arbiter.camera_id, "reason": reason}, "ok")

    def _latch(self, arbiter: MotionArbiter, action: Motion) -> None:
        zoom = action.zoom_direction * self._motion().joystick_zoom_velocity
        velocity = Velocity(action.pan, action.tilt, zoom)
        args = {"pan": action.pan, "tilt": action.tilt, "zoom": zoom}
        result = self._carry_out(
            arbiter, "move", args, lambda: arbiter.move(self.source, velocity, latched=True)
        )
        self._latched = arbiter if result is not None and result["moving"] else None

    def _carry_out(
        self,
        arbiter: MotionArbiter,
        command: str,
        args: dict[str, Any],
        call: Callable[[], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Run an arbiter call and log it; a joystick move is logged only when it starts.

        (Logging every speed change of a moving stick would flood the log.)
        """
        logged_args = {"camera": arbiter.camera_id, **args}
        try:
            result = call()
        except MotionRefusedError as refused:
            self._warn_now_and_then(f"refused:{arbiter.camera_id}", "%s", refused)
            self._log_command(command, logged_args, f"error: {refused}")
            return None
        if result is None or result.get("started", True):
            self._log_command(command, logged_args, "ok")
        return result

    def _warn_now_and_then(self, key: str, message: str, *args: object) -> None:
        """Log a warning at most once a minute per `key`, not once per frame."""
        now = self._clock.monotonic()
        if now - self._warned_at.get(key, float("-inf")) >= REPEATED_WARNING_INTERVAL_SECONDS:
            self._warned_at[key] = now
            self._logger.warning(message, *args)
