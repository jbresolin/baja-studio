"""The cameras module's commands, and which of them the command log keeps.

Motion: `move {camera, pan, tilt, zoom, ttl_ms?}`, `stop {camera, release?}`,
`nudge {camera}`, `recall_preset {camera, n}`, `store_preset {camera, n}`,
`rename_preset {camera, n, label}`, `set_mode {camera, mode, profile?}`.
Configuration helpers: `list_serial_ports {}`, `raw_frames {controller}`.

A browser holding a control sends `move` ten times a second. The command
log keeps the start of each move and the first refusal of a run of
refusals, not every repeat (like the joystick's, ADR 0003 decision 18).
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, TypeVar

from baja_studio.agents.client import AgentError
from baja_studio.agents.contract import Mode
from baja_studio.cameras.config import CamerasSettings, Owner, PresetName
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.inputs.serial_ports import SerialPortLister
from baja_studio.cameras.motion.arbiter import MotionArbiter, MotionRefusedError
from baja_studio.cameras.registry import CameraRegistry
from baja_studio.core.module import CommandError

AGENT_REPLY_TIMEOUT_SECONDS = 3.0
# The configuration page's Nudge: a slow pan right that the ttl ends 0.2 s later.
NUDGE_VELOCITY = Velocity(pan=0.2)
NUDGE_TTL_MS = 200
# Reads, not actions: nothing worth finding in the log after an event.
UNLOGGED_COMMANDS = {"list_serial_ports", "raw_frames"}
MAX_REMEMBERED_MOVE_OUTCOMES = 1000

T = TypeVar("T")


class CameraCommands:
    """Carries out the cameras module's commands against the running registry."""

    def __init__(
        self,
        registry: Callable[[], CameraRegistry | None],
        settings: Callable[[], CamerasSettings],
        save_cameras: Callable[[list[dict[str, Any]]], None],
        list_ports: SerialPortLister,
    ) -> None:
        """`save_cameras(cameras)` saves an edited camera list through the settings store."""
        self._registry = registry
        self._settings = settings
        self._save_cameras = save_cameras
        self._list_ports = list_ports
        self._lock = threading.Lock()
        self._move_outcomes: dict[tuple[str, str], str] = {}

    def handle(self, command: str, args: dict[str, Any], source: str) -> Any:
        """Run one command; raises `CommandError` with a message for the operator."""
        if command == "list_serial_ports":
            return self._serial_ports()
        if command == "raw_frames":
            return self._raw_frames(args)
        arbiter = self._arbiter(args)
        try:
            return self._motion_command(arbiter, command, args, source)
        except MotionRefusedError as refused:
            raise CommandError(str(refused)) from None

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Whether the command log keeps this command (see the module docstring)."""
        if command in UNLOGGED_COMMANDS:
            return False
        if command == "stop" and not failed and isinstance(result, dict):
            return bool(result.get("stopped"))  # a release of a camera this source did not own
        if command != "move":
            return True
        if failed:
            outcome = "refused"
        elif isinstance(result, dict) and result.get("started"):
            outcome = "started"
        else:
            outcome = "continuing"
        with self._lock:
            if len(self._move_outcomes) >= MAX_REMEMBERED_MOVE_OUTCOMES:
                self._move_outcomes.clear()
            key = (source, str(args.get("camera")))
            previous, self._move_outcomes[key] = self._move_outcomes.get(key), outcome
        return outcome == "started" or (outcome == "refused" and previous != "refused")

    # --- motion ------------------------------------------------------------------------

    def _motion_command(
        self, arbiter: MotionArbiter, command: str, args: dict[str, Any], source: str
    ) -> Any:
        if command == "move":
            velocity = Velocity(_number(args, "pan"), _number(args, "tilt"), _number(args, "zoom"))
            ttl_ms = args.get("ttl_ms")
            return arbiter.move(source, velocity, None if ttl_ms is None else int(ttl_ms))
        if command == "stop":
            # `release`: the browser let go of a control. Like a joystick stop
            # frame it stops only a camera this source moves, so letting go
            # after a refused move does not stop the source that refused it.
            release = args.get("release") is True
            return {"stopped": arbiter.stop(source, only_if_owner=release)}
        if command == "nudge":
            return arbiter.move(source, NUDGE_VELOCITY, NUDGE_TTL_MS)
        if command == "recall_preset":
            return arbiter.recall_preset(source, _preset(args))
        if command == "store_preset":
            arbiter.store_preset(source, _preset(args))
            return {"stored": _preset(args)}
        if command == "rename_preset":
            return self._rename_preset(arbiter, args, source)
        if command == "set_mode":
            return self._set_mode(arbiter, args, source)
        raise CommandError(
            f"cameras has no command {command!r}; try move, stop, nudge, recall_preset, "
            "store_preset, rename_preset, set_mode, list_serial_ports or raw_frames"
        )

    def _set_mode(self, arbiter: MotionArbiter, args: dict[str, Any], source: str) -> Any:
        try:
            mode = Mode(str(args.get("mode")))
        except ValueError:
            raise CommandError("mode must be manual, auto or hold") from None
        if mode == Mode.AUTO_PAUSED:
            raise CommandError("auto_paused cannot be asked for; it follows studio motion")
        profile = args.get("profile")
        if profile is not None and not isinstance(profile, dict):
            raise CommandError("profile must be an object")
        reply = _agent_answer(arbiter.set_mode(source, mode, profile))
        return reply.model_dump(mode="json")

    def _rename_preset(self, arbiter: MotionArbiter, args: dict[str, Any], source: str) -> Any:
        """Agent cameras: the agent keeps the names. Direct cameras: the registry does.

        An empty label removes a direct camera's name (the page then shows
        "Preset n+1"); the agent API has no such thing, so it is refused there.
        """
        n, label = _preset(args), str(args.get("label") or "").strip()
        if len(label) > 60:
            raise CommandError("a preset name is at most 60 characters")
        camera = next(c for c in self._settings().cameras if c.id == arbiter.camera_id)
        if camera.owner == Owner.AGENT:
            if not label:
                raise CommandError("a preset name cannot be empty")
            return _agent_answer(arbiter.label_preset(source, n, label)).model_dump(mode="json")
        arbiter.check_preset_number(n)
        presets = [preset for preset in camera.presets if preset.n != n]
        if label:
            presets.append(PresetName(n=n, label=label))
        cameras = [
            c.model_dump(mode="json")
            if c.id != camera.id
            else {
                **c.model_dump(mode="json"),
                "presets": [p.model_dump() for p in sorted(presets, key=lambda p: p.n)],
            }
            for c in self._settings().cameras
        ]
        self._save_cameras(cameras)
        return {"n": n, "label": label}

    # --- configuration helpers --------------------------------------------------------

    def _serial_ports(self) -> dict[str, Any]:
        controllers = {c.port: c.id for c in self._settings().controllers}
        return {
            "ports": [
                {
                    "device": port.device,
                    "description": port.description,
                    "hwid": port.hwid,
                    "used_by": controllers.get(port.device),
                }
                for port in self._list_ports()
            ]
        }

    def _raw_frames(self, args: dict[str, Any]) -> dict[str, Any]:
        controller_id = str(args.get("controller") or "")
        registry = self._registry()
        controller = registry.controller(controller_id) if registry else None
        if controller is None:
            raise CommandError(f"no enabled controller called {controller_id!r}")
        return {"frames": controller.raw_frames()}

    def _arbiter(self, args: dict[str, Any]) -> MotionArbiter:
        camera_id = str(args.get("camera") or "")
        registry = self._registry()
        arbiters = registry.arbiters() if registry is not None else {}
        arbiter, known = arbiters.get(camera_id), sorted(arbiters)
        if arbiter is None:
            hint = f"; cameras: {', '.join(known)}" if known else "; none is enabled"
            raise CommandError(f"no enabled camera called {camera_id!r}{hint}")
        return arbiter


def _agent_answer(future: "Future[T]") -> T:
    """Wait for an agent's answer, turning every way it can fail into a `CommandError`."""
    try:
        return future.result(AGENT_REPLY_TIMEOUT_SECONDS)
    except MotionRefusedError as refused:
        raise CommandError(str(refused)) from None
    except AgentError as error:
        raise CommandError(f"the agent refused: {error}") from None
    except FutureTimeoutError:
        raise CommandError("the agent did not answer in time") from None
    except Exception as error:
        raise CommandError(str(error)) from None


def _number(args: dict[str, Any], key: str) -> float:
    value = args.get(key, 0.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError(f"{key} must be a number from -1 to 1")
    if not -1.0 <= value <= 1.0:
        raise CommandError(f"{key} must be from -1 to 1")
    return float(value)


def _preset(args: dict[str, Any]) -> int:
    value = args.get("n")
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("n must be a whole preset number (0 is the first)")
    return value
