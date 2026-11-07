"""The cameras module's readiness checks: every enabled camera and controller.

They read what the drivers and controller workers already publish (a direct
camera is `ok` while it answers VISCA inquiries; an agent camera while its
agent is linked and lists it), so a check sends nothing to any device.
"""

from datetime import datetime, timedelta
from functools import partial
from typing import Any

from baja_studio.cameras.config import CameraConfig, CamerasSettings, ControllerConfig, DriverKind
from baja_studio.core.clock import Clock
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning

CAMERA_SETUP_LINK = "/cameras/config"
AGENTS_LINK = "/modules/agents"
FRAMES_RECENT_SECONDS = 60.0


def camera_checks(
    settings: CamerasSettings, cameras_state: dict[str, Any], clock: Clock
) -> list[ReadinessCheck]:
    """One check per enabled camera and controller; presets for cameras that need them.

    `cameras_state` is the module's state subtree, read once when the run starts.
    """
    checks = []
    for camera in settings.cameras:
        if not camera.enabled:
            continue
        state = (cameras_state.get("cameras") or {}).get(camera.id) or {}
        name = camera.label or camera.id
        checks.append(
            ReadinessCheck(
                f"camera:{camera.id}",
                f"Camera {name}",
                partial(_camera, state),
                fix=_camera_fix(camera),
                link=AGENTS_LINK if camera.driver == DriverKind.AGENT else CAMERA_SETUP_LINK,
            )
        )
        if camera.needs_presets:
            checks.append(
                ReadinessCheck(
                    f"presets:{camera.id}",
                    f"Camera {name} has presets",
                    partial(_presets, camera, state),
                    fix=(
                        "Store the presets this camera needs (Cameras page → Presets), or untick "
                        "'needs presets' on the Camera setup page."
                    ),
                    link=CAMERA_SETUP_LINK,
                )
            )
    for controller in settings.controllers:
        if not controller.enabled:
            continue
        state = (cameras_state.get("controllers") or {}).get(controller.id) or {}
        checks.append(
            ReadinessCheck(
                f"controller:{controller.id}",
                f"Joystick controller {controller.id}",
                partial(_controller, state, clock),
                fix=_controller_fix(controller),
                link=CAMERA_SETUP_LINK,
            )
        )
    return checks


def _camera(state: dict[str, Any]) -> CheckOutcome:
    status = state.get("status") or "offline"
    reason = state.get("status_reason") or ""
    if status == "ok" and state.get("route") == "fallback":
        return warning(reason or "its agent is away; the studio drives it directly")
    if status == "ok":
        return passed(f"answering ({state.get('route', 'direct')})")
    return failed(f"{status}: {reason}" if reason else status)


def _camera_fix(camera: CameraConfig) -> str:
    if camera.driver == DriverKind.AGENT:
        return (
            f"Check the laptop of agent {camera.address.agent_id} is on, on the network and "
            "its agent running (Agents page), and that it lists camera "
            f"{camera.address.remote_camera_id}."
        )
    return (
        f"Check the camera's power and network cable; the studio expects it at "
        f"{camera.address.host} (Camera setup page)."
    )


def _presets(camera: CameraConfig, state: dict[str, Any]) -> CheckOutcome:
    # An agent camera's presets are the agent's, mirrored into the state.
    presets = state.get("presets") if camera.driver == DriverKind.AGENT else camera.presets
    count = len(presets or [])
    if count == 0:
        return warning("no presets stored")
    return passed(f"{count} preset{'s' if count != 1 else ''}")


def _controller(state: dict[str, Any], clock: Clock) -> CheckOutcome:
    status = state.get("status") or "offline"
    if status != "ok":
        reason = state.get("status_reason") or ""
        return failed(f"{status}: {reason}" if reason else status)
    frames = state.get("recent_frames") or []
    last = _parse_time(frames[0].get("ts")) if frames else None
    if last is None or clock.now_utc() - last > timedelta(seconds=FRAMES_RECENT_SECONDS):
        return warning(
            "port open, but no frames in the last minute: move the joystick to check the wiring",
            "Move the joystick; if still nothing arrives, check the RS485 wiring and the "
            "baud rate.",
        )
    return passed(f"port {state.get('port', '')} open, frames arriving")


def _controller_fix(controller: ControllerConfig) -> str:
    return (
        f"Check the RS485 adapter is plugged in and shows as {controller.port} "
        "(Camera setup page lists the serial ports this PC has)."
    )


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
