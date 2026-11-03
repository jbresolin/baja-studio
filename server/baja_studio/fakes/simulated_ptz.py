"""A simulated camera for the fake agent: position, presets, modes, tracker, image, crop.

Pure and synchronous: every method is told the current time (`Moment`), and
the position is worked out from the velocity when asked, so there are no
timers to go wrong. The mode rules are the contract's (docs/agent-api.md,
"Mode semantics"); events go into `pending_events` for the agent to send.
Motion lives in `simulated_head.py`, the mode rules in `simulated_modes.py`,
and shared types in `simulated_common.py`.
"""

from dataclasses import dataclass, field
from typing import Any

from baja_studio.agents.contract import (
    CameraInfo,
    CameraKind,
    CameraState,
    CommandReply,
    CropRect,
    ErrorCode,
    EventName,
    ImageControlChange,
    ImageControlValue,
    Mode,
    ModeReply,
    ModeRequest,
    MoveRequest,
    Position,
    Preset,
    Status,
)
from baja_studio.fakes.simulated_common import (
    FIXED_LIMITS,
    MOVABLE_KINDS,
    PRESET_MAX,
    PTZ_LIMITS,
    CameraCommandError,
    FakeTracker,
    Moment,
)
from baja_studio.fakes.simulated_crop import CROP_LIMITS, CropWindow
from baja_studio.fakes.simulated_head import SimulatedHead
from baja_studio.fakes.simulated_image import webcam_image_controls
from baja_studio.fakes.simulated_modes import ModeRules


@dataclass
class SimulatedCamera:
    """One camera behind the fake agent."""

    id: str
    label: str
    kind: CameraKind
    pending_events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.modes = ModeRules(self._emit)
        self.online = True
        self.presets: dict[int, Preset] = {}
        self._stored_positions: dict[int, tuple[float, float, float]] = {}
        self.image = webcam_image_controls() if self.kind != CameraKind.FIXED else {}
        self.head = SimulatedHead(self.kind)
        self._last_command_source: str | None = None
        self._last_command_ts: str | None = None

    @property
    def mode(self) -> Mode:
        """The camera's current mode (the rules live in `simulated_modes.py`)."""
        return self.modes.mode

    @property
    def restore_notice(self) -> tuple[str, dict[str, Any]] | None:
        """The notice a restart left because it could not restore auto, if any."""
        return self.modes.restore_notice

    # --- what the agent reports ---------------------------------------------------

    def info(self) -> CameraInfo:
        """The static description for `/agent/info`."""
        movable = self.kind in MOVABLE_KINDS
        limits = {CameraKind.PTZ: PTZ_LIMITS, CameraKind.CROP: CROP_LIMITS}
        return CameraInfo(
            id=self.id,
            label=self.label,
            kind=self.kind,
            driver="simulated",
            has_position_feedback=movable,
            has_zoom=movable,
            supports_auto=movable,
            image_controls=list(self.image),
            presets=[self.presets[n] for n in sorted(self.presets)],
            limits=limits.get(self.kind, FIXED_LIMITS),
        )

    def state(self, now: Moment) -> CameraState:
        """Live state for the WebSocket push."""
        self.head.integrate(now.seconds)
        return CameraState(
            status=Status.OK if self.online else Status.OFFLINE,
            mode=self.mode,
            tracker=self.modes.tracker_state(),
            position=self.head.position(),
            moving=self.head.moving(now.seconds),
            last_command_source=self._last_command_source,
            last_command_ts=self._last_command_ts,
        )

    def advance(self, now: Moment) -> None:
        """Called every push cycle: apply the time-based rule (auto_paused → auto)."""
        self.head.integrate(now.seconds)
        self.modes.advance(now.seconds)

    # --- motion ----------------------------------------------------------------------

    def move(self, now: Moment, request: MoveRequest, source: str) -> CommandReply:
        """Velocity move for `ttl_ms` (a PTZ's head, or a crop camera's window); zeros stop."""
        self._require_movable()
        if request.pan == request.tilt == request.zoom == 0:
            return self.stop(now, source)
        until = now.seconds + request.ttl_ms / 1000
        self.head.drive(now.seconds, (request.pan, request.tilt, request.zoom), until)
        self._studio_motion(now, source, motion_ends_at=until)
        return CommandReply(accepted=True, mode=self.mode)

    def stop(self, now: Moment, source: str) -> CommandReply:
        """Stop every axis now; refused only when the device is offline."""
        self._require_online()
        self.head.halt(now.seconds)
        if self.kind in MOVABLE_KINDS:
            self._studio_motion(now, source, motion_ends_at=now.seconds)
        return CommandReply(accepted=True, mode=self.mode)

    def recall_preset(self, now: Moment, n: int, source: str) -> CommandReply:
        """Jump to a stored preset (the fake has no travel time)."""
        self._require_preset_number(n)
        preset_position = self._stored_positions.get(n)
        if preset_position is None:
            raise CameraCommandError(ErrorCode.UNKNOWN_PRESET, f"preset {n} is not stored")
        self.head.jump_to(now.seconds, preset_position)
        self._studio_motion(now, source, motion_ends_at=now.seconds)
        return CommandReply(accepted=True, mode=self.mode)

    def store_preset(self, now: Moment, n: int) -> Preset:
        """Remember the current position as preset `n`."""
        self._require_preset_number(n)
        self.head.integrate(now.seconds)
        self._stored_positions[n] = self.head.pan_tilt_zoom()
        existing = self.presets.get(n)
        self.presets[n] = Preset(n=n, label=existing.label if existing else f"Preset {n}")
        self._emit(EventName.PRESETS_CHANGED, {"n": n})
        return self.presets[n]

    def label_preset(self, n: int, label: str) -> Preset:
        """Rename a stored preset."""
        self._require_preset_number(n)
        if n not in self.presets:
            raise CameraCommandError(ErrorCode.UNKNOWN_PRESET, f"preset {n} is not stored")
        self.presets[n] = Preset(n=n, label=label)
        self._emit(EventName.PRESETS_CHANGED, {"n": n})
        return self.presets[n]

    def position(self, now: Moment) -> Position | None:
        """Where the camera points, or None for kinds without position feedback."""
        self._require_online()
        self.head.integrate(now.seconds)
        return self.head.position()

    # --- mode and tracker ------------------------------------------------------------

    def mode_reply(self) -> ModeReply:
        """Answer of `GET /mode`."""
        return self.modes.reply()

    def set_mode(self, request: ModeRequest) -> ModeReply:
        """`PUT /mode`: manual, auto or hold, with an optional new profile."""
        self._require_online()
        return self.modes.set_mode(request, self.id, has_tracker=self.kind != CameraKind.FIXED)

    def set_tracker(self, tracker: FakeTracker) -> None:
        """Fake control: make the tracker not ready, ready (searching) or tracking."""
        self.modes.set_tracker(tracker)

    def restart(self, now: Moment) -> None:
        """Fake control: the agent restarts. Motion stops and the last mode is restored."""
        self.head.halt(now.seconds)
        self.pending_events.clear()  # a restarted agent has forgotten them
        self.modes.restart(now.iso)

    def set_online(self, now: Moment, online: bool) -> None:
        """Fake control: unplug or re-plug the device behind the agent."""
        if online == self.online:
            return
        self.head.halt(now.seconds)
        self.online = online
        self._emit(EventName.DEVICE_ONLINE if online else EventName.DEVICE_OFFLINE, {})

    # --- image, crop, resync -----------------------------------------------------------

    def image_values(self) -> dict[str, ImageControlValue]:
        """Every image control as read back."""
        self._require_online()
        return {name: control.read_back() for name, control in self.image.items()}

    def set_image(self, changes: dict[str, ImageControlChange]) -> dict[str, ImageControlValue]:
        """Apply changes (all or nothing) and return what took."""
        self._require_online()
        unknown = sorted(set(changes) - set(self.image))
        if unknown:
            raise CameraCommandError(
                ErrorCode.UNKNOWN_CONTROL,
                f"{self.id} has no image control {', '.join(unknown)}; "
                f"it has {', '.join(self.image) or 'none'}",
            )
        for name, change in changes.items():
            self.image[name].apply(change)
        return self.image_values()

    def crop(self, now: Moment) -> CropRect:
        """The current crop of a crop camera, wherever `/move` has taken it."""
        self._require_kind(CameraKind.CROP)
        self.head.integrate(now.seconds)
        return self.head.crop_window.rect()

    def set_crop(self, now: Moment, rect: CropRect, source: str) -> CropRect:
        """Jump the window to `rect`: a studio motion command, like a preset recall."""
        self._require_kind(CameraKind.CROP)
        self._require_online()
        self.head.halt(now.seconds)
        self.head.crop_window = CropWindow.from_rect(rect)
        self._studio_motion(now, source, motion_ends_at=now.seconds)
        return self.head.crop_window.rect()

    def resync(self, now: Moment) -> CommandReply:
        """Re-read the device (the fake's device never drifts, so nothing changes)."""
        self._require_online()
        self.head.integrate(now.seconds)
        return CommandReply(accepted=True, mode=self.mode)

    # --- internals ---------------------------------------------------------------------

    def _studio_motion(self, now: Moment, source: str, motion_ends_at: float) -> None:
        self._last_command_source = source
        self._last_command_ts = now.iso
        self.modes.studio_motion(source, motion_ends_at)

    def _emit(self, event: EventName, detail: dict[str, Any]) -> None:
        self.pending_events.append((event.value, detail))

    def _require_online(self) -> None:
        if not self.online:
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, f"{self.label} is not answering")

    def _require_kind(self, kind: CameraKind) -> None:
        if self.kind != kind:
            raise CameraCommandError(
                ErrorCode.NOT_SUPPORTED, f"{self.id} is a {self.kind} camera, not {kind}"
            )

    def _require_movable(self) -> None:
        if self.kind not in MOVABLE_KINDS:
            raise CameraCommandError(ErrorCode.NOT_SUPPORTED, f"{self.id} is a fixed camera")
        self._require_online()

    def _require_preset_number(self, n: int) -> None:
        """Presets are for PTZs, numbered 0 to `PRESET_MAX`."""
        self._require_kind(CameraKind.PTZ)
        self._require_online()
        if n > PRESET_MAX:
            raise CameraCommandError(
                ErrorCode.BAD_REQUEST, f"{self.id} stores presets 0 to {PRESET_MAX}, not {n}"
            )
