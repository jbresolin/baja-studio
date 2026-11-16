"""What the camera node's tray icon reads: `GET /api/node/tray-status`, from this laptop only.

(ADR 0023.)

The tray (`baja_studio_tray --node`) runs as the logged-on user and cannot
know the node's token: the token is a secret setting, stored by the service
and masked everywhere. So the tray gets its own small answer, which:

- needs no token, but is refused (403) to anyone but this laptop itself
  (127.0.0.1 or ::1): the node has no proxy in front, so the client address
  is the real one;
- holds nothing secret: the node's status and version, and per camera its
  id, name, video mode, status and the status's short reason, with the
  camera's output URL, its OBS password and the node's token scrubbed out
  of the reason once more, in case a reason ever quoted one.

    {"status": "ok" | "degraded", "version": "0.7.0", "name": "XPS",
     "cameras": [{"id": "cam1", "label": "Left S600", "mode": "node",
                  "status": "ok" | "degraded" | "offline", "reason": ""}]}

The node's port is a setting (8891 unless changed on its page), stored in
the service's database, which the tray does not open. So the node writes
the port it serves on to `<data>/port.txt` each time it starts, and the
tray reads that file (8891 when it is missing).
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from baja_studio.node.redact import scrub, secret_parts
from baja_studio.node.settings import NodeCameraSettings, NodeSettings

# The node module's own routes are served under /api/node (the studio's rule for module routes).
TRAY_STATUS_ROUTE = "/tray-status"
TRAY_STATUS_PATH = f"/api/node{TRAY_STATUS_ROUTE}"
if TYPE_CHECKING:  # module.py imports this file to serve the route
    from baja_studio.node.module import NodeModule

LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})
PORT_FILE_NAME = "port.txt"

logger = logging.getLogger(__name__)


def write_port_file(data_directory: Path, port: int) -> None:
    """Tell the tray which port the node serves on (`<data>/port.txt`); never fatal."""
    try:
        data_directory.mkdir(parents=True, exist_ok=True)
        (data_directory / PORT_FILE_NAME).write_text(f"{port}\n", encoding="ascii")
    except OSError as error:
        logger.warning("could not write %s for the tray: %s", PORT_FILE_NAME, error)


def is_from_this_computer(request: Request) -> bool:
    """True when the request comes from the laptop itself (loopback)."""
    client = request.client
    return client is not None and client.host in LOOPBACK_ADDRESSES


def camera_secrets(camera: NodeCameraSettings, settings: NodeSettings) -> list[str]:
    """What must never appear in a camera's reason: its output URL's parts, its OBS
    password and the node's token."""
    return [*secret_parts(camera.video.output_url), camera.obs.password, settings.token]


def tray_status(
    module: "NodeModule", health: dict[str, Any], settings: NodeSettings
) -> dict[str, Any]:
    """The tray's answer, built from the node's health and its served cameras."""
    cameras = []
    served = module.cameras
    for camera_settings in settings.served():
        camera = None if served is None else served.camera(camera_settings.id)
        if camera is None:
            status, reason = "offline", "starting"
        else:
            camera_status, reason = camera.status()
            status = camera_status.value
        cameras.append(
            {
                "id": camera_settings.id,
                "label": camera_settings.label or camera_settings.id,
                "mode": camera_settings.mode.value,
                "status": status,
                "reason": scrub(reason, camera_secrets(camera_settings, settings)),
            }
        )
    return {
        "status": health.get("status"),
        "version": health.get("version"),
        "name": settings.name,
        "cameras": cameras,
    }


def tray_status_router(module: "NodeModule") -> APIRouter:
    """`GET /tray-status` for `module`; app.py's token middleware lets this one path through."""
    router = APIRouter()

    @router.get(TRAY_STATUS_ROUTE)
    def get_tray_status(request: Request) -> Any:
        if not is_from_this_computer(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "only the tray on this laptop may read this"},
            )
        services = request.app.state.services
        settings = services.settings.get("node")
        assert isinstance(settings, NodeSettings)
        return tray_status(module, services.registry.health_report(), settings)

    return router
