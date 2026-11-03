"""A camera driver that only writes down what it was asked to do, for arbiter tests."""

from concurrent.futures import Future
from typing import Any

from baja_studio.agents.contract import CameraKind, Mode, ModeReply, Position, Status
from baja_studio.cameras.driver import (
    CameraDriver,
    DriverCapabilities,
    ModeCapableDriver,
    Velocity,
)


class RecordingDriver(CameraDriver):
    """Remembers every call as a tuple, e.g. ("move", Velocity(...), 300) or ("stop",)."""

    has_modes = False

    def __init__(self, preset_max: int | None = 127) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.preset_max = preset_max
        self.current_route = "direct"

    def connect(self) -> None:
        self.calls.append(("connect",))

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        self.calls.append(("move", velocity, ttl_ms))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def recall_preset(self, n: int) -> None:
        self.calls.append(("recall_preset", n))

    def store_preset(self, n: int) -> None:
        self.calls.append(("store_preset", n))

    def position(self) -> Position | None:
        return None

    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(CameraKind.PTZ, False, True, self.has_modes, self.preset_max)

    def health(self) -> Status:
        return Status.OK

    @property
    def route(self) -> str:
        return self.current_route

    def names(self) -> list[str]:
        """Just the method names, in order: ["move", "stop", ...]."""
        return [call[0] for call in self.calls]

    def clear(self) -> None:
        self.calls.clear()


class RecordingModeDriver(RecordingDriver, ModeCapableDriver):
    """A recording driver with modes, like an agent-owned camera's."""

    has_modes = True

    def set_mode(self, mode: Mode, profile: dict[str, Any] | None) -> "Future[ModeReply]":
        self.calls.append(("set_mode", mode, profile))
        result: Future[ModeReply] = Future()
        result.set_result(ModeReply(mode=mode, profile=profile or {}))
        return result
