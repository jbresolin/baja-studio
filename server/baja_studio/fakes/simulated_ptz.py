"""A simulated camera for the fake agent: position, presets, modes, tracker, image, crop.

Pure and synchronous: every method is told the current time (`Moment`), and
the position is worked out from the velocity when asked, so there are no
timers to go wrong. The mode rules are the contract's (docs/agent-api.md,
"Mode semantics"); events go into `pending_events` for the agent to send.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from baja_studio.agents.contract import (
    DEFAULT_RESUME_AFTER_MS,
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
    SpeedLimits,
    Status,
    TrackerState,
    TrackerStatus,
)
from baja_studio.fakes.simulated_image import webcam_image_controls

PAN_RANGE = (-170.0, 170.0)
TILT_RANGE = (-30.0, 90.0)
ZOOM_RANGE = (0.0, 1.0)
LIMITS = SpeedLimits(pan_speed_max=100.0, tilt_speed_max=60.0, zoom_speed_max=0.5)
MAX_RESUME_AFTER_MS = 600_000
FAKE_TARGET_ID = "fake-target-1"


class FakeTracker(StrEnum):
    """What the fake tracker can be told to be, via `POST /_fake/cameras/{id}/tracker`."""

    NOT_READY = "not_ready"
    READY = "ready"
    TRACKING = "tracking"


class CameraCommandError(Exception):
    """A command the camera refuses; the agent turns it into `{error, detail}`."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        """`code` decides the HTTP status (contract.ERROR_HTTP_STATUS)."""
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Moment:
    """ "Now", both as monotonic seconds (for arithmetic) and as ISO text (for messages)."""

    seconds: float
    iso: str


def _clamp(value: float, limits: tuple[float, float]) -> float:
    return min(limits[1], max(limits[0], value))


@dataclass
class SimulatedCamera:
    """One camera behind the fake agent."""

    id: str
    label: str
    kind: CameraKind
    pending_events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mode = Mode.MANUAL
        self.profile: dict[str, Any] = {}
        self.tracker = FakeTracker.READY
        self.online = True
        self.presets: dict[int, Preset] = {}
        self._stored_positions: dict[int, tuple[float, float, float]] = {}
        self.image = webcam_image_controls() if self.kind != CameraKind.FIXED else {}
        self.crop_rect = CropRect(x=0.0, y=0.0, w=1.0, h=1.0)
        self._pan, self._tilt, self._zoom = 0.0, 0.0, 0.0
        self._velocity = (0.0, 0.0, 0.0)
        self._velocity_until = 0.0
        self._integrated_at = 0.0
        self._resume_at: float | None = None
        self._last_command_source: str | None = None
        self._last_command_ts: str | None = None

    # --- what the agent reports ---------------------------------------------------

    def info(self) -> CameraInfo:
        """The static description for `/agent/info`."""
        is_ptz = self.kind == CameraKind.PTZ
        return CameraInfo(
            id=self.id,
            label=self.label,
            kind=self.kind,
            driver="simulated",
            has_position_feedback=is_ptz,
            has_zoom=is_ptz,
            supports_auto=self.kind != CameraKind.FIXED,
            image_controls=list(self.image),
            presets=[self.presets[n] for n in sorted(self.presets)],
            limits=LIMITS,
        )

    def state(self, now: Moment) -> CameraState:
        """Live state for the WebSocket push."""
        self._integrate(now)
        moving = now.seconds < self._velocity_until and any(self._velocity)
        return CameraState(
            status=Status.OK if self.online else Status.OFFLINE,
            mode=self.mode,
            tracker=self._tracker_state(),
            position=self._position() if self.kind == CameraKind.PTZ else None,
            moving=moving,
            last_command_source=self._last_command_source,
            last_command_ts=self._last_command_ts,
        )

    def advance(self, now: Moment) -> None:
        """Called every push cycle: apply the time-based rule (auto_paused → auto)."""
        self._integrate(now)
        resume_is_due = self._resume_at is not None and now.seconds >= self._resume_at
        if self.mode == Mode.AUTO_PAUSED and resume_is_due:
            self._change_mode(Mode.AUTO, "resume_after_ms elapsed")
            self._emit(EventName.MANUAL_OVERRIDE_ENDED, {"reason": "timeout"})

    # --- motion ----------------------------------------------------------------------

    def move(self, now: Moment, request: MoveRequest, source: str) -> CommandReply:
        """Velocity move for `ttl_ms`; all zeros is a stop."""
        self._require_ptz()
        if request.pan == request.tilt == request.zoom == 0:
            return self.stop(now, source)
        self._integrate(now)
        self._velocity = (request.pan, request.tilt, request.zoom)
        self._velocity_until = now.seconds + request.ttl_ms / 1000
        self._studio_motion(now, source, motion_ends_at=self._velocity_until)
        return CommandReply(accepted=True, mode=self.mode)

    def stop(self, now: Moment, source: str) -> CommandReply:
        """Stop every axis now; refused only when the device is offline."""
        self._require_online()
        self._integrate(now)
        self._velocity_until = now.seconds
        if self.kind == CameraKind.PTZ:
            self._studio_motion(now, source, motion_ends_at=now.seconds)
        return CommandReply(accepted=True, mode=self.mode)

    def recall_preset(self, now: Moment, n: int, source: str) -> CommandReply:
        """Jump to a stored preset (the fake has no travel time)."""
        self._require_ptz()
        preset_position = self._stored_positions.get(n)
        if preset_position is None:
            raise CameraCommandError(ErrorCode.UNKNOWN_PRESET, f"preset {n} is not stored")
        self._integrate(now)
        self._velocity_until = now.seconds
        self._pan, self._tilt, self._zoom = preset_position
        self._studio_motion(now, source, motion_ends_at=now.seconds)
        return CommandReply(accepted=True, mode=self.mode)

    def store_preset(self, now: Moment, n: int) -> Preset:
        """Remember the current position as preset `n`."""
        self._require_ptz()
        self._integrate(now)
        self._stored_positions[n] = (self._pan, self._tilt, self._zoom)
        existing = self.presets.get(n)
        self.presets[n] = Preset(n=n, label=existing.label if existing else f"Preset {n}")
        self._emit(EventName.PRESETS_CHANGED, {"n": n})
        return self.presets[n]

    def label_preset(self, n: int, label: str) -> Preset:
        """Rename a stored preset."""
        self._require_ptz()
        if n not in self.presets:
            raise CameraCommandError(ErrorCode.UNKNOWN_PRESET, f"preset {n} is not stored")
        self.presets[n] = Preset(n=n, label=label)
        self._emit(EventName.PRESETS_CHANGED, {"n": n})
        return self.presets[n]

    def position(self, now: Moment) -> Position | None:
        """Where the camera points, or None for kinds without position feedback."""
        self._require_online()
        self._integrate(now)
        return self._position() if self.kind == CameraKind.PTZ else None

    # --- mode and tracker ------------------------------------------------------------

    def mode_reply(self) -> ModeReply:
        """Answer of `GET /mode`."""
        return ModeReply(mode=self.mode, profile=self.profile)

    def set_mode(self, request: ModeRequest) -> ModeReply:
        """`PUT /mode`: manual, auto or hold, with an optional new profile."""
        self._require_online()
        if request.mode == Mode.AUTO:
            if self.kind == CameraKind.FIXED:
                raise CameraCommandError(ErrorCode.NOT_SUPPORTED, f"{self.id} has no tracker")
            if self.tracker == FakeTracker.NOT_READY:
                raise CameraCommandError(
                    ErrorCode.TRACKER_NOT_READY, "the tracker is not running for this camera"
                )
        if request.profile is not None:
            self.profile = _checked_profile(request.profile)
        was_paused = self.mode == Mode.AUTO_PAUSED
        self._change_mode(request.mode, "requested by the studio")
        if was_paused and request.mode == Mode.AUTO:
            self._emit(EventName.MANUAL_OVERRIDE_ENDED, {"reason": "auto requested"})
        return self.mode_reply()

    def set_tracker(self, tracker: FakeTracker) -> None:
        """Fake control: make the tracker not ready, ready (searching) or tracking."""
        was_tracking = self._tracker_state().state == TrackerStatus.TRACKING
        self.tracker = tracker
        is_tracking = self._tracker_state().state == TrackerStatus.TRACKING
        if is_tracking and not was_tracking:
            self._emit(EventName.TRACKER_ACQUIRED, {"target_id": FAKE_TARGET_ID})
        elif was_tracking and not is_tracking:
            self._emit(EventName.TRACKER_LOST, {})

    def set_online(self, now: Moment, online: bool) -> None:
        """Fake control: unplug or re-plug the device behind the agent."""
        if online == self.online:
            return
        self._integrate(now)
        self._velocity_until = now.seconds
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

    def crop(self) -> CropRect:
        """The current crop of a crop camera."""
        self._require_kind(CameraKind.CROP)
        return self.crop_rect

    def set_crop(self, rect: CropRect) -> CropRect:
        """Set the crop of a crop camera."""
        self._require_kind(CameraKind.CROP)
        self._require_online()
        self.crop_rect = rect
        return self.crop_rect

    def resync(self, now: Moment) -> CommandReply:
        """Re-read the device (the fake's device never drifts, so nothing changes)."""
        self._require_online()
        self._integrate(now)
        return CommandReply(accepted=True, mode=self.mode)

    # --- internals ---------------------------------------------------------------------

    def _studio_motion(self, now: Moment, source: str, motion_ends_at: float) -> None:
        """Manual wins: any studio motion pauses auto, and restarts the resume countdown."""
        self._last_command_source = source
        self._last_command_ts = now.iso
        if self.mode == Mode.AUTO:
            self._change_mode(Mode.AUTO_PAUSED, "studio motion command")
            self._emit(EventName.MANUAL_OVERRIDE_STARTED, {"source": source})
        if self.mode == Mode.AUTO_PAUSED:
            resume_after_ms = self.profile.get("resume_after_ms", DEFAULT_RESUME_AFTER_MS)
            self._resume_at = motion_ends_at + resume_after_ms / 1000

    def _change_mode(self, mode: Mode, reason: str) -> None:
        if mode == self.mode:
            return
        previous, self.mode = self.mode, mode
        self._emit(EventName.MODE_CHANGED, {"from": previous, "to": mode, "reason": reason})

    def _tracker_state(self) -> TrackerState:
        if self.mode not in (Mode.AUTO, Mode.AUTO_PAUSED):
            return TrackerState(state=TrackerStatus.IDLE)
        if self.tracker == FakeTracker.TRACKING:
            return TrackerState(
                state=TrackerStatus.TRACKING, target_id=FAKE_TARGET_ID, confidence=0.9
            )
        if self.tracker == FakeTracker.READY:
            return TrackerState(state=TrackerStatus.SEARCHING)
        return TrackerState(state=TrackerStatus.LOST)

    def _integrate(self, now: Moment) -> None:
        """Move the position on by the velocity, up to when the velocity expires."""
        until = min(now.seconds, self._velocity_until)
        elapsed = until - self._integrated_at
        if elapsed > 0:
            pan, tilt, zoom = self._velocity
            self._pan = _clamp(self._pan + pan * LIMITS.pan_speed_max * elapsed, PAN_RANGE)
            self._tilt = _clamp(self._tilt + tilt * LIMITS.tilt_speed_max * elapsed, TILT_RANGE)
            self._zoom = _clamp(self._zoom + zoom * LIMITS.zoom_speed_max * elapsed, ZOOM_RANGE)
        self._integrated_at = max(self._integrated_at, now.seconds)

    def _position(self) -> Position:
        return Position(pan=round(self._pan, 3), tilt=round(self._tilt, 3), zoom=self._zoom)

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

    def _require_ptz(self) -> None:
        self._require_kind(CameraKind.PTZ)
        self._require_online()


def _checked_profile(profile: dict[str, Any]) -> dict[str, Any]:
    resume_after_ms = profile.get("resume_after_ms", DEFAULT_RESUME_AFTER_MS)
    if (
        not isinstance(resume_after_ms, int)
        or isinstance(resume_after_ms, bool)
        or not 0 <= resume_after_ms <= MAX_RESUME_AFTER_MS
    ):
        raise CameraCommandError(
            ErrorCode.BAD_REQUEST,
            f"profile.resume_after_ms must be a whole number of ms from 0 to {MAX_RESUME_AFTER_MS}",
        )
    return dict(profile)
