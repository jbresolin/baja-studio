"""The one interface every camera driver implements, and what a driver gets from the module.

The rest of the service sees every camera the same way: normalised velocities
(-1..1, positive pan right, tilt up, zoom in), presets numbered from 0, and a
health of ok / degraded / offline. Only the motion arbiter calls a driver.

Every method returns quickly: drivers queue work for their own worker thread
(or, for the agent driver, the agents module's event loop) and never wait on
the network in the caller's thread.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from baja_studio.agents.contract import CameraKind, Mode, ModeReply, Position, Preset, Status
from baja_studio.core.clock import Clock
from baja_studio.core.supervisor import WorkerSpec

Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


@dataclass(frozen=True)
class Velocity:
    """Normalised velocities, each -1..1."""

    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0

    @property
    def is_zero(self) -> bool:
        """True when nothing would move."""
        return self.pan == 0.0 and self.tilt == 0.0 and self.zoom == 0.0


AT_REST = Velocity()


@dataclass(frozen=True)
class DriverCapabilities:
    """What a camera can do, in the spirit of the contract's `CameraInfo`."""

    kind: CameraKind
    has_position_feedback: bool
    has_zoom: bool
    supports_auto: bool
    preset_max: int | None

    def as_state(self) -> dict[str, Any]:
        """JSON form for the camera's state entry."""
        return {
            "kind": self.kind.value,
            "has_position_feedback": self.has_position_feedback,
            "has_zoom": self.has_zoom,
            "supports_auto": self.supports_auto,
            "preset_max": self.preset_max,
        }


@dataclass
class DriverHost:
    """What the cameras module lends a driver: time, logging, state and workers.

    `publish(key, value)` sets `state.cameras.cameras.<camera id>.<key>`, so a
    driver writes the contract's `CameraState` fields (status, mode, tracker,
    position, moving) one key at a time. Workers started here run under the
    supervisor like any other, and their names should start with
    `worker_prefix` so the module can tell whose they are.
    """

    camera_id: str
    clock: Clock
    logger: Logger
    publish: Callable[[str, Any], None]
    start_worker: Callable[[WorkerSpec], None]
    stop_worker: Callable[[str], None]

    @property
    def worker_prefix(self) -> str:
        """Prefix of this camera's worker names, e.g. "camera:finish-ptz"."""
        return f"camera:{self.camera_id}"


class CameraDriver(ABC):
    """A camera as the arbiter sees it."""

    @abstractmethod
    def connect(self) -> None:
        """Start talking to the camera (start workers). Never sends motion by itself."""

    @abstractmethod
    def disconnect(self) -> None:
        """Stop talking to the camera and wait for the workers. Sends nothing either."""

    @abstractmethod
    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Drive at `velocity` until `ttl_ms` passes without another `move`."""

    @abstractmethod
    def stop(self) -> None:
        """Stop every axis, pan/tilt and zoom alike."""

    @abstractmethod
    def recall_preset(self, n: int) -> None:
        """Go to stored preset `n` (0-based)."""

    @abstractmethod
    def store_preset(self, n: int) -> None:
        """Store the current position as preset `n` (0-based)."""

    @abstractmethod
    def position(self) -> Position | None:
        """The last position read from the camera, or None if unknown."""

    @abstractmethod
    def capabilities(self) -> DriverCapabilities:
        """What the camera can do."""

    @abstractmethod
    def health(self) -> Status:
        """ok, degraded or offline, as last observed."""

    @property
    def route(self) -> str:
        """Which way commands go right now ("direct", "agent", "fallback")."""
        return "direct"

    def settled(self) -> bool:
        """True once nothing is moving and every stop has been confirmed.

        Used before closing a driver that has been driving a camera, so the
        camera is not left moving. Drivers without confirmations say True.
        """
        return True


class ModeCapableDriver(CameraDriver):
    """A driver that also has the agent's modes (manual / auto / hold)."""

    @abstractmethod
    def set_mode(self, mode: Mode, profile: dict[str, Any] | None) -> "Future[ModeReply]":
        """Ask for a mode; the future fails with the agent's refusal, if any."""

    @abstractmethod
    def label_preset(self, n: int, label: str) -> "Future[Preset]":
        """Rename a stored preset on the agent (it keeps agent cameras' preset names)."""
