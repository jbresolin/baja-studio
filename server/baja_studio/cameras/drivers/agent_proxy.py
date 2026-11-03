"""Agent-proxied driver: a camera owned by a laptop agent, driven through its API (WP1).

Every verb is forwarded to `/cameras/{remote id}/...` through the agents
module (which holds the one `AgentClient` per agent, on its event loop). The
camera's state is mirrored from that agent's live state, so an agent camera
looks exactly like a direct one in `state.cameras.cameras.<id>`.

Commands go out one at a time, in order (`agent_commands.py`). While the
agent is unreachable, motion commands are dropped: the agent's own ttl has
stopped the camera, and a backlog would only replay old motion later.

Direct fallback (contract §8): if the agent has been `offline` for
`direct_fallback_after_s` and the camera has a `direct_fallback`, the driver
starts that direct driver and routes every command to it. When the agent is
back, it stops the direct driver (after a stop, if it was moving), routes to
the agent again and asks it to `/resync`.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from baja_studio.agents.contract import CameraKind, Mode, ModeReply, Position, Preset, Status
from baja_studio.cameras.config import DirectFallback
from baja_studio.cameras.driver import (
    CameraDriver,
    DriverCapabilities,
    DriverHost,
    ModeCapableDriver,
    Velocity,
)
from baja_studio.cameras.drivers.agent_commands import MOVE, STOP, AgentCommandQueue
from baja_studio.cameras.drivers.agent_gateway import AgentCall, AgentGateway, AgentLinkView
from baja_studio.core.supervisor import HealthState, WorkerContext, WorkerSpec

TICK_SECONDS = 0.1
# How long a returning fallback driver may take to confirm its stop before it is closed.
FALLBACK_STOP_GRACE_SECONDS = 1.0
MIRRORED_FIELDS = (
    "mode",
    "tracker",
    "position",
    "moving",
    "last_command_source",
    "last_command_ts",
)
ROUTE_AGENT, ROUTE_FALLBACK = "agent", "fallback"


class AgentProxyDriver(ModeCapableDriver):
    """One camera that a laptop agent owns."""

    def __init__(
        self,
        host: DriverHost,
        gateway: AgentGateway,
        agent_id: str,
        remote_camera_id: str,
        fallback: DirectFallback | None,
        make_fallback: Callable[[DirectFallback], CameraDriver],
    ) -> None:
        """`make_fallback` builds the direct driver used while the agent is away."""
        self._host = host
        self._gateway = gateway
        self._agent_id = agent_id
        self._remote_id = remote_camera_id
        self._fallback_config = fallback
        self._make_fallback = make_fallback
        self.worker_name = f"{host.worker_prefix}:agent"
        self._lock = threading.Lock()
        self._commands = AgentCommandQueue(gateway, agent_id, self._command_failed)
        self._view: AgentLinkView | None = None
        self._offline_since: float | None = None
        self._fallback: CameraDriver | None = None
        self._returning: tuple[CameraDriver, float] | None = None
        self._route = ROUTE_AGENT
        self._health = (Status.OFFLINE, "starting")
        self._published: dict[str, Any] = {}

    # --- CameraDriver -------------------------------------------------------------

    @property
    def route(self) -> str:
        """ "agent", or "fallback" while driving the camera directly."""
        return self._route

    def connect(self) -> None:
        """Start following the agent's state. Sends nothing (no stop on connect, §7)."""
        self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop following the agent, and close a fallback driver if one is open."""
        self._host.stop_worker(self.worker_name)
        with self._lock:
            drivers = [driver for driver in (self._fallback, self._returning_driver()) if driver]
            self._fallback, self._returning = None, None
        for driver in drivers:
            driver.disconnect()

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Forward a velocity; the agent stops it after `ttl_ms` (its dead-man)."""
        target = self._direct_target()
        if target is not None:
            target.move(velocity, ttl_ms)
            return
        remote, pan, tilt, zoom = self._remote_id, velocity.pan, velocity.tilt, velocity.zoom
        self._forward(MOVE, lambda client: client.move(remote, pan, tilt, zoom, ttl_ms))

    def stop(self) -> None:
        """Forward a stop; queued moves are dropped, they would only undo it."""
        target = self._direct_target()
        if target is not None:
            target.stop()
            return
        remote = self._remote_id
        self._forward(STOP, lambda client: client.stop(remote))

    def recall_preset(self, n: int) -> None:
        """Forward a preset recall."""
        target = self._direct_target()
        if target is not None:
            target.recall_preset(n)
            return
        remote = self._remote_id
        self._forward("recall_preset", lambda client: client.recall_preset(remote, n))

    def store_preset(self, n: int) -> None:
        """Forward a preset store."""
        target = self._direct_target()
        if target is not None:
            target.store_preset(n)
            return
        remote = self._remote_id
        self._forward("store_preset", lambda client: client.store_preset(remote, n))

    def set_mode(self, mode: Mode, profile: dict[str, Any] | None) -> "Future[ModeReply]":
        """Ask the agent for a mode. Refused while driving directly (there is no tracker)."""
        result: Future[ModeReply] = Future()
        if self._refused_in_fallback(result):
            return result
        remote = self._remote_id
        self._forward("set_mode", lambda client: client.set_mode(remote, mode, profile), result)
        return result

    def label_preset(self, n: int, label: str) -> "Future[Preset]":
        """Rename a preset on the agent. Refused while driving directly (the agent keeps them)."""
        result: Future[Preset] = Future()
        if self._refused_in_fallback(result):
            return result
        remote = self._remote_id
        self._forward("label_preset", lambda client: client.label_preset(remote, n, label), result)
        return result

    def _refused_in_fallback(self, result: "Future[Any]") -> bool:
        if self._route != ROUTE_FALLBACK:
            return False
        result.set_exception(
            RuntimeError("the agent is offline; its camera is being driven directly")
        )
        return True

    def mode(self) -> str | None:
        """The camera's mode as the agent last reported it."""
        state = self._remote_state()
        return None if state is None else state.get("mode")

    def position(self) -> Position | None:
        """Mirrored from the agent (or read from the fallback driver)."""
        target = self._direct_target()
        if target is not None:
            return target.position()
        state = self._remote_state()
        position = None if state is None else state.get("position")
        return None if position is None else Position.model_validate(position)

    def _info(self) -> dict[str, Any] | None:
        """This camera's entry in the agent's `/agent/info`, once it has been read."""
        view = self._view
        for camera in (view.info or {}).get("cameras", []) if view else []:
            if camera.get("id") == self._remote_id:
                return dict(camera)
        return None

    def capabilities(self) -> DriverCapabilities:
        """From the agent's `/agent/info`; a PTZ with no presets until it has been read."""
        camera = self._info()
        if camera is not None:
            return DriverCapabilities(
                kind=CameraKind(camera["kind"]),
                has_position_feedback=camera["has_position_feedback"],
                has_zoom=camera["has_zoom"],
                supports_auto=camera["supports_auto"],
                preset_max=camera["limits"]["preset_max"],
            )
        return DriverCapabilities(CameraKind.PTZ, False, True, True, None)

    def health(self) -> Status:
        """The camera's health through whichever route is in use."""
        return self._health[0]

    # --- the follow worker ---------------------------------------------------------

    def _run(self, worker: WorkerContext) -> None:
        while not worker.should_stop:
            self._follow(self._host.clock.monotonic())
            status, reason = self._health
            worker.report_health(
                {Status.OK: HealthState.OK, Status.DEGRADED: HealthState.DEGRADED}.get(
                    status, HealthState.OFFLINE
                ),
                reason,
            )
            worker.sleep(TICK_SECONDS)

    def _follow(self, now: float) -> None:
        """One look at the agent: offline timer, route changes, mirrored state, health."""
        view = self._gateway.link(self._agent_id)
        self._view = view
        status = view.status if view else "missing"
        if status == "offline":
            self._offline_since = self._offline_since if self._offline_since is not None else now
        else:
            self._offline_since = None
        self._finish_returning(now)
        if self._route == ROUTE_AGENT and self._fallback_due(now):
            self._start_fallback()
        elif self._route == ROUTE_FALLBACK and status == "ok":
            self._return_to_agent(now)
        self._health = self._judge(view)
        self._mirror()

    def _fallback_due(self, now: float) -> bool:
        if self._fallback_config is None or self._offline_since is None:
            return False
        # The tolerance: ten 0.1 s ticks add up to 0.9999999999999999 s.
        return now - self._offline_since >= self._gateway.direct_fallback_after_s() - 1e-6

    def _start_fallback(self) -> None:
        assert self._fallback_config is not None
        self._host.logger.warning(
            "camera %s: agent %s offline; driving it directly", self._host.camera_id, self._agent_id
        )
        driver = self._make_fallback(self._fallback_config)
        driver.connect()
        self._commands.clear()
        with self._lock:
            self._fallback, self._route = driver, ROUTE_FALLBACK

    def _return_to_agent(self, now: float) -> None:
        self._host.logger.info(
            "camera %s: agent %s is back; handing the camera back",
            self._host.camera_id,
            self._agent_id,
        )
        with self._lock:
            driver, self._fallback, self._route = self._fallback, None, ROUTE_AGENT
        if driver is not None:
            driver.stop()  # the camera has no dead-man: never leave it moving
            self._returning = (driver, now)
        remote = self._remote_id
        self._forward("resync", lambda client: client.resync(remote), always=True)

    def _finish_returning(self, now: float) -> None:
        if self._returning is None:
            return
        driver, since = self._returning
        if driver.settled() or now - since >= FALLBACK_STOP_GRACE_SECONDS:
            self._returning = None
            driver.disconnect()

    def _returning_driver(self) -> CameraDriver | None:
        return self._returning[0] if self._returning else None

    def _judge(self, view: AgentLinkView | None) -> tuple[Status, str]:
        if self._route == ROUTE_FALLBACK and self._fallback is not None:
            return self._fallback.health(), f"agent {self._agent_id} offline; driving directly"
        if view is None:
            return Status.OFFLINE, f"agent {self._agent_id} is not configured"
        if view.status == "ok":
            state = view.cameras.get(self._remote_id)
            if state is None:
                return Status.OFFLINE, f"agent {self._agent_id} lists no camera {self._remote_id}"
            camera_status = Status(state["status"])
            return camera_status, "" if camera_status == Status.OK else "reported by the agent"
        if view.status == "degraded":
            return Status.DEGRADED, f"agent {self._agent_id}: {view.reason}"
        return Status.OFFLINE, f"agent {self._agent_id} {view.status}" + (
            f": {view.reason}" if view.reason else ""
        )

    def _mirror(self) -> None:
        status, reason = self._health
        fields: dict[str, Any] = {
            "status": status.value,
            "status_reason": reason,
            "route": self._route,
            "capabilities": self.capabilities().as_state(),
            # The agent keeps this camera's preset names; they stay listed in fallback.
            "presets": (self._info() or {}).get("presets", []),
        }
        target = self._fallback if self._route == ROUTE_FALLBACK else None
        if target is not None:
            position = target.position()
            fields.update(
                mode="manual",
                tracker={"state": "idle", "target_id": None, "confidence": None},
                position=position.model_dump() if position else None,
            )
        else:
            state = self._remote_state() or {}
            fields.update({key: state.get(key) for key in MIRRORED_FIELDS})
        # Status last, so a page that sees "ok" also sees the mode and position with it.
        fields["status"] = fields.pop("status")
        for key, value in fields.items():
            if self._published.get(key, ...) != value:
                self._published[key] = value
                self._host.publish(key, value)

    def _remote_state(self) -> dict[str, Any] | None:
        view = self._view
        return None if view is None else view.cameras.get(self._remote_id)

    def _direct_target(self) -> CameraDriver | None:
        with self._lock:
            return self._fallback if self._route == ROUTE_FALLBACK else None

    # --- commands to the agent ---------------------------------------------------------

    def _forward(
        self,
        kind: str,
        call: AgentCall,
        result: "Future[Any] | None" = None,
        always: bool = False,
    ) -> None:
        """Send a command to the agent, unless it is unreachable and nobody waits for it."""
        view = self._view
        if not always and result is None and (view is None or view.status != "ok"):
            return
        self._commands.send(kind, call, result)

    def _command_failed(self, kind: str, error: BaseException) -> None:
        self._host.logger.warning(
            "camera %s: %s via agent %s failed: %s",
            self._host.camera_id,
            kind,
            self._agent_id,
            error,
        )
        self._host.publish("last_error", f"{kind}: {error}")
