"""The contract's mode rules for one simulated camera (docs/agent-api.md §7 and §9).

Studio motion pauses auto and restarts the resume countdown; `auto_paused`
returns to `auto` by itself after `resume_after_ms`; `hold` and `manual` end
auto until the studio asks for it; a restart restores the last mode, or
`manual` with a notice if the tracker cannot run. Events go out through the
`emit` callback the camera passes in.
"""

from collections.abc import Callable
from typing import Any

from baja_studio.agents.contract import (
    DEFAULT_RESUME_AFTER_MS,
    RESTORE_REASON_TRACKER_NOT_READY,
    ErrorCode,
    EventName,
    Mode,
    ModeReply,
    ModeRequest,
    TrackerState,
    TrackerStatus,
)
from baja_studio.fakes.simulated_common import (
    FAKE_TARGET_ID,
    CameraCommandError,
    FakeTracker,
    checked_profile,
    tracker_state,
)

Emit = Callable[[EventName, dict[str, Any]], None]


class ModeRules:
    """One camera's mode, behaviour profile, tracker and resume countdown."""

    def __init__(self, emit: Emit) -> None:
        """Start in `manual` with a ready tracker."""
        self.mode = Mode.MANUAL
        self.profile: dict[str, Any] = {}
        self.tracker = FakeTracker.READY
        # Set when a restart could not restore auto: (ts, mode_changed detail).
        self.restore_notice: tuple[str, dict[str, Any]] | None = None
        self._resume_at: float | None = None
        self._emit = emit

    def tracker_state(self) -> TrackerState:
        """What the tracker reports in the camera's state."""
        return tracker_state(self.mode, self.tracker)

    def reply(self) -> ModeReply:
        """Answer of `GET /mode`."""
        return ModeReply(mode=self.mode, profile=self.profile)

    def advance(self, now_seconds: float) -> None:
        """The time-based rule: `auto_paused` becomes `auto` once the countdown is over."""
        resume_is_due = self._resume_at is not None and now_seconds >= self._resume_at
        if self.mode == Mode.AUTO_PAUSED and resume_is_due:
            self._change(Mode.AUTO, "resume_after_ms elapsed")
            self._emit(EventName.MANUAL_OVERRIDE_ENDED, {"reason": "timeout"})

    def studio_motion(self, source: str, motion_ends_at: float) -> None:
        """Manual wins: any studio motion pauses auto, and restarts the resume countdown."""
        if self.mode == Mode.AUTO:
            self._change(Mode.AUTO_PAUSED, "studio motion command")
            self._emit(EventName.MANUAL_OVERRIDE_STARTED, {"source": source})
        if self.mode == Mode.AUTO_PAUSED:
            resume_after_ms = self.profile.get("resume_after_ms", DEFAULT_RESUME_AFTER_MS)
            self._resume_at = motion_ends_at + resume_after_ms / 1000

    def set_mode(self, request: ModeRequest, camera_id: str, has_tracker: bool) -> ModeReply:
        """`PUT /mode`: manual, auto or hold, with an optional new profile."""
        if request.mode == Mode.AUTO:
            if not has_tracker:
                raise CameraCommandError(ErrorCode.NOT_SUPPORTED, f"{camera_id} has no tracker")
            if self.tracker == FakeTracker.NOT_READY:
                raise CameraCommandError(
                    ErrorCode.TRACKER_NOT_READY, "the tracker is not running for this camera"
                )
        if request.profile is not None:
            self.profile = checked_profile(request.profile)
        was_paused = self.mode == Mode.AUTO_PAUSED
        self.restore_notice = None  # a studio has chosen the mode: it knows
        self._change(request.mode, "requested by the studio")
        if was_paused and request.mode == Mode.AUTO:
            self._emit(EventName.MANUAL_OVERRIDE_ENDED, {"reason": "auto requested"})
        return self.reply()

    def set_tracker(self, tracker: FakeTracker) -> None:
        """Fake control: make the tracker not ready, ready (searching) or tracking."""
        was_tracking = self.tracker_state().state == TrackerStatus.TRACKING
        self.tracker = tracker
        is_tracking = self.tracker_state().state == TrackerStatus.TRACKING
        if is_tracking and not was_tracking:
            self._emit(EventName.TRACKER_ACQUIRED, {"target_id": FAKE_TARGET_ID})
        elif was_tracking and not is_tracking:
            self._emit(EventName.TRACKER_LOST, {})

    def restart(self, now_iso: str) -> None:
        """The agent restarted: restore the last mode (a pause does not survive).

        If auto cannot run because the tracker is not ready, the camera comes
        back in `manual` and keeps a notice saying why (contract §9). Every
        socket closes on a restart, so the notice is kept for each studio that
        connects, until the mode next changes or a studio sets it.
        """
        self._resume_at = None
        self.mode = Mode.AUTO if self.mode == Mode.AUTO_PAUSED else self.mode
        if self.mode == Mode.AUTO and self.tracker == FakeTracker.NOT_READY:
            self.mode = Mode.MANUAL
            detail = {
                "from": Mode.AUTO,
                "to": Mode.MANUAL,
                "reason": RESTORE_REASON_TRACKER_NOT_READY,
            }
            self.restore_notice = (now_iso, detail)

    def _change(self, mode: Mode, reason: str) -> None:
        if mode == self.mode:
            return
        previous, self.mode = self.mode, mode
        self.restore_notice = None
        self._emit(EventName.MODE_CHANGED, {"from": previous, "to": mode, "reason": reason})
