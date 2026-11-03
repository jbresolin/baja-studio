"""What the fake agent's simulated cameras share: ranges, limits, errors, the time, the tracker.

Split out of `simulated_ptz.py`, which keeps the camera itself (motion,
presets, modes, image and crop).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from baja_studio.agents.contract import (
    DEFAULT_RESUME_AFTER_MS,
    CameraKind,
    CameraLimits,
    ErrorCode,
    Mode,
    TrackerState,
    TrackerStatus,
)

PAN_RANGE = (-170.0, 170.0)
TILT_RANGE = (-30.0, 90.0)
ZOOM_RANGE = (0.0, 1.0)
# Below the wire's 255 on purpose, so tests can tell the two limits apart.
PRESET_MAX = 127
PTZ_LIMITS = CameraLimits(
    pan_speed_max=100.0, tilt_speed_max=60.0, zoom_speed_max=0.5, preset_max=PRESET_MAX
)
FIXED_LIMITS = CameraLimits(
    pan_speed_max=0.0, tilt_speed_max=0.0, zoom_speed_max=0.0, preset_max=None
)
MOVABLE_KINDS = (CameraKind.PTZ, CameraKind.CROP)
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


def clamp(value: float, limits: tuple[float, float]) -> float:
    """`value` kept inside `limits` (low, high)."""
    return min(limits[1], max(limits[0], value))


def tracker_state(mode: Mode, tracker: "FakeTracker") -> TrackerState:
    """What the tracker reports: idle outside auto, else searching, tracking or lost."""
    if mode not in (Mode.AUTO, Mode.AUTO_PAUSED):
        return TrackerState(state=TrackerStatus.IDLE)
    if tracker == FakeTracker.TRACKING:
        return TrackerState(state=TrackerStatus.TRACKING, target_id=FAKE_TARGET_ID, confidence=0.9)
    if tracker == FakeTracker.READY:
        return TrackerState(state=TrackerStatus.SEARCHING)
    return TrackerState(state=TrackerStatus.LOST)


def checked_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """A copy of a mode profile, refused if `resume_after_ms` is not 0-600000 whole ms."""
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
