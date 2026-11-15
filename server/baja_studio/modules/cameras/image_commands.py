"""The cameras module's image-settings commands (WP6a), and which of them the command log keeps.

- `list_usb_devices {}`: the UVC child's status and the USB cameras plugged
  in (`name`, `path`, `index`, `used_by`); asks the child to look first.
- `get_image {camera}`: read the camera's controls afresh.
- `set_image {camera, controls: {name: {value?, auto?}}}`: write now, save
  what the device took; a refused value is shown on its control.
- `lock_image {camera}`: what the camera chose on auto, saved as manual.
- `reset_image {camera}`: the device's defaults (USB cameras only).
- `copy_image {from, to}`: the controls both have, clamped to the target.
- `simulate_usb {action, path?, light?, pan?, tilt?}`: the fake UVC backend's
  switches (`unplug`, `replug`, `scene`, `hand_move`), for the rig and demos.

A slider being dragged sends `set_image` several times a second. Like
camera moves, the log keeps the start of a run of writes to the same
controls (a pause of more than a second starts a new run), not every step.
"""

import contextlib
import threading
from typing import Any

from pydantic import ValidationError

from baja_studio.core.childproc import ChildRequestError
from baja_studio.core.clock import Clock
from baja_studio.core.module import CommandError
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.imaging.config import ImageSetting
from baja_studio.imaging.service import ImageRequestError, ImagingService
from baja_studio.imaging.sources import ImageUnavailableError
from baja_studio.imaging.watch import ImagingWatch

IMAGE_COMMANDS = frozenset(
    {
        "list_usb_devices",
        "get_image",
        "set_image",
        "lock_image",
        "reset_image",
        "copy_image",
        "simulate_usb",
    }
)
# Reads: nothing worth finding in the log after an event.
UNLOGGED_IMAGE_COMMANDS = frozenset({"list_usb_devices", "get_image"})
# Writes to the same controls closer together than this are one run (one log line).
RUN_GAP_SECONDS = 1.0
FAKE_ACTIONS = ("unplug", "replug", "scene", "hand_move")
MAX_REMEMBERED_RUNS = 1000


class ImageCommands:
    """Carries out the image commands against the imaging service."""

    def __init__(self, service: ImagingService, watch: ImagingWatch, clock: Clock) -> None:
        """`clock` times the runs of writes for the command log."""
        self._service = service
        self._watch = watch
        self._clock = clock
        self._lock = threading.Lock()
        self._runs: dict[tuple[str, str], tuple[tuple[str, ...], float]] = {}

    def handle(self, command: str, args: dict[str, Any], source: str) -> Any:
        """Run one command; raises `CommandError` with a message for the operator."""
        try:
            return self._handle(command, args)
        except (ImageRequestError, ImageUnavailableError) as error:
            raise CommandError(str(error)) from None

    def wants_logged(self, command: str, args: dict[str, Any], source: str) -> bool:
        """Whether the command log keeps this command (see the module docstring)."""
        if command in UNLOGGED_IMAGE_COMMANDS:
            return False
        if command != "set_image":
            return True
        controls = args.get("controls")
        names = tuple(sorted(controls)) if isinstance(controls, dict) else ()
        key = (source, str(args.get("camera")))
        now = self._clock.monotonic()
        with self._lock:
            if len(self._runs) >= MAX_REMEMBERED_RUNS:
                self._runs.clear()
            previous, self._runs[key] = self._runs.get(key), (names, now)
        return previous is None or previous[0] != names or now - previous[1] > RUN_GAP_SECONDS

    def _handle(self, command: str, args: dict[str, Any]) -> Any:
        service = self._service
        if command == "list_usb_devices":
            # With the child down the answer's status says so; nothing to refresh.
            with contextlib.suppress(DeviceOfflineError, ChildRequestError):
                service.link.refresh_devices()
            return self._watch.usb_state()
        if command == "get_image":
            return service.get(_camera(args, "camera"))
        if command == "set_image":
            return service.set(_camera(args, "camera"), _changes(args.get("controls")))
        if command == "lock_image":
            return service.lock(_camera(args, "camera"))
        if command == "reset_image":
            return service.reset(_camera(args, "camera"))
        if command == "copy_image":
            return service.copy(_camera(args, "from"), _camera(args, "to"))
        if command == "simulate_usb":
            return self._simulate(args)
        raise CommandError(f"cameras has no image command {command!r}")

    def _simulate(self, args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "")
        if action not in FAKE_ACTIONS:
            raise CommandError(f"action must be one of {', '.join(FAKE_ACTIONS)}")
        params = {key: value for key, value in args.items() if key != "action"}
        try:
            result = self._service.link.request(f"fake_{action}", params)
        except DeviceOfflineError as error:
            raise CommandError(str(error)) from None
        except ChildRequestError as error:
            raise CommandError(f"the UVC child refused: {error}") from None
        self._watch.wake()
        return {"done": action, "result": result}


def _camera(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise CommandError(f"{key} must be a camera id")
    return value


def _changes(controls: Any) -> dict[str, ImageSetting]:
    if not isinstance(controls, dict) or not controls:
        raise CommandError('controls must be an object like {"gain": {"value": 10}}')
    changes = {}
    for name, change in controls.items():
        try:
            changes[str(name)] = ImageSetting.model_validate(change)
        except ValidationError:
            raise CommandError(
                f"{name}: give a number as value and/or true or false as auto"
            ) from None
    return changes
