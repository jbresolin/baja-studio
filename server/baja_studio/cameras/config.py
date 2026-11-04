"""The camera registry and controller list, as stored in the cameras module's settings.

Everything here is edited in the browser (WP3) and applied without a restart.
Velocities, zoom and scales are normalised; device tables live in the drivers.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baja_studio.agents.contract import DEFAULT_TTL_MS, MAX_PRESET_NUMBER, MAX_TTL_MS
from baja_studio.cameras.drivers import visca_packets
from baja_studio.core.settings import ModuleSettings

VISCA_IP_PORT = 52381
PELCO_D_DEFAULT_BAUD = 2400
MAX_CALL_BUTTON = 9
# No dots: ids become parts of dotted state paths (state.cameras.cameras.<id>.status).
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"


class Owner(StrEnum):
    """Who holds the camera's connection: the studio itself, or a laptop agent."""

    STUDIO = "studio"
    AGENT = "agent"


class DriverKind(StrEnum):
    """Which driver speaks to the camera. Pixy and OBS crop arrive in WP6."""

    VISCA_IP = "visca_ip"
    AGENT = "agent"


class SpeedCurve(StrEnum):
    """How a normalised velocity is shaped before it reaches the driver."""

    LINEAR = "linear"
    SQUARED = "squared"  # fine control near the centre of the stick


class StrictModel(BaseModel):
    """Settings items refuse unknown keys, so a typo is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid")


class CameraAddress(StrictModel):
    """Where a camera is: `host`/`port` for direct drivers, `agent_id`/`remote_camera_id`
    for a camera owned by a laptop agent."""

    host: str = Field(default="", max_length=253, description="IP address (direct drivers).")
    port: int = Field(default=VISCA_IP_PORT, ge=1, le=65535)
    agent_id: str = Field(default="", description="Agent id from the agents page.")
    remote_camera_id: str = Field(default="", description="The camera's id on that agent.")


class PresetName(StrictModel):
    """An operator's name for a preset number (0-based, as VISCA and the agent API count)."""

    n: int = Field(ge=0, le=MAX_PRESET_NUMBER)
    label: str = Field(min_length=1, max_length=60)


class MotionLimits(StrictModel):
    """Per-camera scaling and direction of the three axes."""

    pan_speed_scale: float = Field(default=1.0, ge=0.0, le=1.0)
    tilt_speed_scale: float = Field(default=1.0, ge=0.0, le=1.0)
    zoom_speed_scale: float = Field(default=1.0, ge=0.0, le=1.0)
    invert_pan: bool = False
    invert_tilt: bool = False
    # Device steps, not normalised: what a velocity of 1.0 becomes in a VISCA
    # packet. They apply to direct VISCA/IP drivers (and an agent camera's
    # direct fallback); an agent applies its own limits.
    pan_speed_max_step: int = Field(
        default=visca_packets.DEFAULT_PAN_SPEED_MAX_STEP,
        ge=visca_packets.PAN_SPEED_MIN,
        le=visca_packets.PAN_SPEED_MAX,
        description="Top VISCA pan speed step (1-24, 0x18).",
    )
    tilt_speed_max_step: int = Field(
        default=visca_packets.DEFAULT_TILT_SPEED_MAX_STEP,
        ge=visca_packets.TILT_SPEED_MIN,
        le=visca_packets.TILT_SPEED_MAX,
        description="Top VISCA tilt speed step (1-23, 0x17).",
    )


class DirectFallback(StrictModel):
    """How to drive an agent-owned camera directly while its agent is away (contract §8)."""

    driver: DriverKind = DriverKind.VISCA_IP
    address: CameraAddress

    @model_validator(mode="after")
    def _direct_driver_with_a_host(self) -> Self:
        if self.driver == DriverKind.AGENT:
            raise ValueError("a direct fallback must be a direct driver, not 'agent'")
        if not self.address.host:
            raise ValueError("a direct fallback needs the camera's host")
        return self


class CameraConfig(StrictModel):
    """One camera in the registry."""

    id: str = Field(pattern=ID_PATTERN, description="Short name, e.g. finish-ptz.")
    label: str = Field(default="", max_length=60)
    enabled: bool = True
    owner: Owner = Owner.STUDIO
    driver: DriverKind = DriverKind.VISCA_IP
    address: CameraAddress = Field(default_factory=CameraAddress)
    controller_address: int | None = Field(
        default=None, ge=1, le=255, description="Pelco-D address that selects this camera."
    )
    obs_source: str | None = None
    presets: list[PresetName] = Field(default_factory=list)
    limits: MotionLimits = Field(default_factory=MotionLimits)
    speed_curve: SpeedCurve = SpeedCurve.LINEAR
    direct_fallback: DirectFallback | None = None

    @model_validator(mode="after")
    def _owner_matches_driver(self) -> Self:
        if (self.owner == Owner.AGENT) != (self.driver == DriverKind.AGENT):
            raise ValueError("agent-owned cameras use the 'agent' driver, and only they do")
        if self.driver == DriverKind.AGENT:
            if not (self.address.agent_id and self.address.remote_camera_id):
                raise ValueError("an agent camera needs address.agent_id and remote_camera_id")
        elif not self.address.host:
            raise ValueError("a direct camera needs address.host")
        if self.direct_fallback is not None and self.owner != Owner.AGENT:
            raise ValueError("only agent-owned cameras have a direct fallback")
        return self

    def connection_key(self) -> tuple[object, ...]:
        """What the driver is built from; a change here means a new driver."""
        return (self.driver, self.address, self.direct_fallback)


class ControllerProtocol(StrEnum):
    """Serial controller protocols; Pelco-P and VISCA serial could join later."""

    PELCO_D = "pelco_d"


class CallAction(StrEnum):
    """What a Call button press does."""

    NONE = "none"
    # Sets the controller's camera selection, which a Cameras page can follow.
    SELECT_CAMERA = "select_camera"
    # Recalls a preset on the controller's selected camera.
    RECALL_PRESET = "recall_preset"


class CallBinding(StrictModel):
    """What the controller's Call button `n` does."""

    n: int = Field(ge=1, le=MAX_CALL_BUTTON)
    action: CallAction = CallAction.NONE
    camera: str | None = Field(default=None, description="For select_camera: the camera id.")
    preset: int | None = Field(
        default=None, ge=0, le=MAX_PRESET_NUMBER, description="For recall_preset (0-based)."
    )

    @model_validator(mode="after")
    def _target_for_the_action(self) -> Self:
        if self.action == CallAction.SELECT_CAMERA and not self.camera:
            raise ValueError(f"Call {self.n}: select_camera needs a camera")
        if self.action == CallAction.RECALL_PRESET and self.preset is None:
            raise ValueError(f"Call {self.n}: recall_preset needs a preset number")
        return self


class ControllerConfig(StrictModel):
    """One serial joystick controller."""

    id: str = Field(pattern=ID_PATTERN)
    port: str = Field(min_length=1, description="Serial port, e.g. COM8 or /dev/ttyUSB0.")
    baud: int = Field(default=PELCO_D_DEFAULT_BAUD, ge=300, le=115200)
    protocol: ControllerProtocol = ControllerProtocol.PELCO_D
    enabled: bool = True
    call_bindings: list[CallBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_binding_per_button(self) -> Self:
        numbers = [binding.n for binding in self.call_bindings]
        if len(set(numbers)) != len(numbers):
            raise ValueError("a Call button is bound twice")
        return self

    def call_binding(self, n: int) -> CallBinding | None:
        """The binding of Call button `n`, or None when it does nothing."""
        for binding in self.call_bindings:
            if binding.n == n and binding.action != CallAction.NONE:
                return binding
        return None


class MotionSettings(StrictModel):
    """How velocity commands are held, repeated and filtered."""

    joystick_deadband: float = Field(
        default=0.04, ge=0.0, le=0.5, description="Joystick deflection treated as rest."
    )
    ttl_ms: int = Field(
        default=DEFAULT_TTL_MS,
        ge=50,
        le=MAX_TTL_MS,
        description="A velocity not renewed for this long is stopped (the dead-man).",
    )
    resend_interval_ms: int = Field(
        default=100, ge=20, le=1000, description="How often a held velocity is sent again."
    )
    joystick_zoom_velocity: float = Field(
        default=0.6,
        gt=0.0,
        le=1.0,
        description="Zoom speed for the controller's zoom buttons (they send no speed).",
    )
    controller_silence_ms: int = Field(
        default=0,
        ge=0,
        le=60000,
        description=(
            "Stop the joystick's camera when its controller sends nothing for this long "
            "(0: off). Only for controllers that repeat frames while a stick is held; "
            "2000 if the bench shows ours does."
        ),
    )

    @model_validator(mode="after")
    def _resend_inside_ttl(self) -> Self:
        if self.resend_interval_ms >= self.ttl_ms:
            raise ValueError("resend_interval_ms must be shorter than ttl_ms")
        return self


class CamerasSettings(ModuleSettings):
    """Settings of the cameras module."""

    # Edited on the Camera configuration page (/cameras/config), so the generic
    # Settings form leaves them out. The list order is the Cameras page's order.
    cameras: list[CameraConfig] = Field(
        default_factory=list,
        description="The camera registry, in display order.",
        json_schema_extra={"managed_elsewhere": True},
    )
    controllers: list[ControllerConfig] = Field(
        default_factory=list,
        description="Serial joystick controllers.",
        json_schema_extra={"managed_elsewhere": True},
    )
    motion: MotionSettings = Field(
        default_factory=MotionSettings,
        description=(
            "Deadband, velocity ttl, resend interval, joystick zoom speed, controller silence."
        ),
    )

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        camera_ids = [camera.id for camera in self.cameras]
        if len(set(camera_ids)) != len(camera_ids):
            raise ValueError("two cameras have the same id")
        addresses = [
            camera.controller_address
            for camera in self.cameras
            if camera.enabled and camera.controller_address is not None
        ]
        if len(set(addresses)) != len(addresses):
            raise ValueError("two enabled cameras answer to the same controller address")
        controller_ids = [controller.id for controller in self.controllers]
        if len(set(controller_ids)) != len(controller_ids):
            raise ValueError("two controllers have the same id")
        ports = [controller.port for controller in self.controllers if controller.enabled]
        if len(set(ports)) != len(ports):
            raise ValueError("two enabled controllers use the same serial port")
        for controller in self.controllers:
            for binding in controller.call_bindings:
                if binding.action == CallAction.SELECT_CAMERA and binding.camera not in camera_ids:
                    raise ValueError(
                        f"controller {controller.id}: Call {binding.n} selects camera "
                        f"{binding.camera!r}, which is not in the list"
                    )
        return self
