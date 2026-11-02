"""The agent API contract 1.0 as Pydantic models and constants.

docs/agent-api.md is the normative text; this file is its machine-readable
twin, used by the studio's client, by the fake agent and by the conformance
tests. Change both together, and bump `CONTRACT_VERSION` when you do.

Models ignore fields they do not know, so an agent speaking a newer minor
version (which may only add fields) is still understood.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0"
AGENT_PORT = 8890
TOKEN_HEADER = "X-Agent-Token"
STUDIO_ID_HEADER = "X-Studio-Id"

# Timings every implementation must respect (see docs/agent-api.md, "Timings").
MIN_STATE_PUSH_HZ = 2.0
OFFLINE_AFTER_SILENCE_SECONDS = 5.0
DEFAULT_TTL_MS = 300
MAX_TTL_MS = 2000
DEFAULT_RESUME_AFTER_MS = 3000
INFO_ANSWER_WITHIN_SECONDS = 0.3


def contract_major(version: str) -> int | None:
    """The MAJOR part of a "MAJOR.MINOR" version, or None if it is not one."""
    major, _, minor = version.partition(".")
    if not (major.isdigit() and minor.isdigit()):
        return None
    return int(major)


class CameraKind(StrEnum):
    """What sort of camera an agent drives."""

    PTZ = "ptz"
    CROP = "crop"
    FIXED = "fixed"


class Status(StrEnum):
    """Health of an agent or of one camera behind it."""

    OK = "ok"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class Mode(StrEnum):
    """Who drives a camera: the studio (manual, hold) or the tracker (auto)."""

    MANUAL = "manual"
    AUTO = "auto"
    AUTO_PAUSED = "auto_paused"
    HOLD = "hold"


class TrackerStatus(StrEnum):
    """What the tracker for a camera is doing."""

    IDLE = "idle"
    SEARCHING = "searching"
    TRACKING = "tracking"
    LOST = "lost"


class ErrorCode(StrEnum):
    """Every `error` value an agent may answer with, and its HTTP status."""

    BAD_REQUEST = "bad_request"  # 400
    NOT_SUPPORTED = "not_supported"  # 400: e.g. /crop on a PTZ camera
    UNKNOWN_CONTROL = "unknown_control"  # 400
    UNAUTHORIZED = "unauthorized"  # 401
    UNKNOWN_CAMERA = "unknown_camera"  # 404
    UNKNOWN_PRESET = "unknown_preset"  # 404
    TRACKER_NOT_READY = "tracker_not_ready"  # 409
    DEVICE_OFFLINE = "device_offline"  # 503


ERROR_HTTP_STATUS = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.NOT_SUPPORTED: 400,
    ErrorCode.UNKNOWN_CONTROL: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.UNKNOWN_CAMERA: 404,
    ErrorCode.UNKNOWN_PRESET: 404,
    ErrorCode.TRACKER_NOT_READY: 409,
    ErrorCode.DEVICE_OFFLINE: 503,
}


class EventName(StrEnum):
    """The `event` values of WebSocket event messages."""

    MODE_CHANGED = "mode_changed"
    MANUAL_OVERRIDE_STARTED = "manual_override_started"
    MANUAL_OVERRIDE_ENDED = "manual_override_ended"
    TRACKER_ACQUIRED = "tracker_acquired"
    TRACKER_LOST = "tracker_lost"
    DEVICE_OFFLINE = "device_offline"
    DEVICE_ONLINE = "device_online"
    PRESETS_CHANGED = "presets_changed"


class ContractModel(BaseModel):
    """Base for every wire model: unknown fields are ignored, not errors."""

    model_config = ConfigDict(extra="ignore")


# --- agent level -----------------------------------------------------------------


class Preset(ContractModel):
    """A stored preset: its number and the operator's name for it."""

    n: int = Field(ge=0, le=255)
    label: str


class SpeedLimits(ContractModel):
    """Top speeds at velocity 1.0: degrees per second, and zoom range per second."""

    pan_speed_max: float
    tilt_speed_max: float
    zoom_speed_max: float


class CameraInfo(ContractModel):
    """What a camera is and can do; changes only on reconnect or `presets_changed`."""

    id: str
    label: str
    kind: CameraKind
    driver: str
    has_position_feedback: bool
    has_zoom: bool
    supports_auto: bool
    image_controls: list[str]
    presets: list[Preset]
    limits: SpeedLimits


class AgentInfo(ContractModel):
    """Answer of `GET /agent/info`: who the agent is and which cameras it owns."""

    agent_id: str
    name: str
    contract_version: str
    software_version: str
    cameras: list[CameraInfo]
    capabilities: list[str] = Field(default_factory=list)


class AgentHealth(ContractModel):
    """Answer of `GET /agent/health`."""

    status: Status
    detail: str = ""
    uptime_s: float
    cameras: dict[str, Status]


class LogEntry(ContractModel):
    """One line of `GET /agent/log`: the same shape as the studio command log."""

    ts: str
    module: str
    source: str
    command: str
    args: dict[str, Any]
    result: str


# --- camera state --------------------------------------------------------------


class Position(ContractModel):
    """Pan and tilt in degrees from the device's home; zoom 0 (wide) to 1 (tele)."""

    pan: float
    tilt: float
    zoom: float = Field(ge=0.0, le=1.0)


class TrackerState(ContractModel):
    """The tracker's view of one camera."""

    state: TrackerStatus
    target_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CameraState(ContractModel):
    """Live state of one camera, pushed over the WebSocket."""

    status: Status
    mode: Mode
    tracker: TrackerState
    position: Position | None
    moving: bool
    last_command_source: str | None = None
    last_command_ts: str | None = None


class StateMessage(ContractModel):
    """Agent → studio, at least twice a second: every camera's state."""

    type: Literal["state"] = "state"
    ts: str
    cameras: dict[str, CameraState]


class EventMessage(ContractModel):
    """Agent → studio, when something changes; `camera_id` is None for agent-wide events."""

    type: Literal["event"] = "event"
    ts: str
    camera_id: str | None
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)


# --- requests and replies --------------------------------------------------------


class MoveRequest(ContractModel):
    """Body of `POST /cameras/{id}/move`: normalised velocities and a time-to-live."""

    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=-1.0, le=1.0)
    ttl_ms: int = Field(default=DEFAULT_TTL_MS, ge=1, le=MAX_TTL_MS)


class CommandReply(ContractModel):
    """Answer to /move, /stop, preset recall and /resync."""

    accepted: bool
    mode: Mode


class PresetLabelRequest(ContractModel):
    """Body of `PUT /cameras/{id}/presets/{n}`."""

    label: str = Field(min_length=1, max_length=60)


class ModeRequest(ContractModel):
    """Body of `PUT /cameras/{id}/mode`; `auto_paused` cannot be asked for."""

    mode: Literal[Mode.MANUAL, Mode.AUTO, Mode.HOLD]
    profile: dict[str, Any] | None = None


class ModeReply(ContractModel):
    """Answer of `GET` and `PUT /cameras/{id}/mode`."""

    mode: Mode
    profile: dict[str, Any]


class ImageControlChange(ContractModel):
    """One control in the body of `PUT /cameras/{id}/image`; omitted parts stay as they are."""

    value: float | None = None
    auto: bool | None = None
    locked: bool | None = None


class ImageControlValue(ContractModel):
    """One control as read back from the device, with its range."""

    value: float
    auto: bool | None
    locked: bool
    min: float
    max: float
    step: float


class CropRect(ContractModel):
    """A crop of the source frame, all four numbers as fractions 0..1 of it."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside_the_frame(self) -> "CropRect":
        # A small tolerance so 0.1 + 0.9 (= 1.0000000000000002) still counts as inside.
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError("the crop must lie inside the frame: x + w <= 1 and y + h <= 1")
        return self


class ErrorBody(ContractModel):
    """Every error answer: a snake_case code and a sentence for the operator."""

    error: str
    detail: str
