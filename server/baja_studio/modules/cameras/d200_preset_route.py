"""Recall a camera preset by opening a URL (for the Ulanzi D200's buttons).

    GET or POST /action/preset/{camera}/{number}
        {"ok":true,"camera":"PTZ1","preset":1}

- `camera` is the camera's id, or its label, in any case (`PTZ1`, `ptz1`).
- `number` is the preset as the Cameras page shows it, counting from 1.
  (The `recall_preset` command counts from 0: the URL's 1 is the command's 0.)
- Errors are `{"error": "…"}` with status 400, or 404 for a camera that
  isn't there, like the D200's audio buttons (`modules/audio/d200_routes.py`).

The recall runs as `device:d200`, so it is in the command log next to the
D200's audio buttons, and goes through the same checks as a recall from the
Cameras page (the camera must be enabled; an agent's camera answers through
its agent).
"""

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from baja_studio.core.api import services_of
from baja_studio.core.commandlog import device_source
from baja_studio.core.module import CommandError

D200_SOURCE = device_source("d200")
MODULE = "cameras"
BAD_NUMBER = "the preset number counts from 1, as on the Cameras page"


def respond(body: dict[str, Any], status: int = 200) -> Response:
    """Compact JSON, the content type the D200's audio routes answer with."""
    return Response(
        json.dumps(body, separators=(",", ":")).encode("utf-8"),
        status_code=status,
        media_type="application/json; charset=utf-8",
    )


def camera_id_for(cameras: list[Any], asked: str) -> str | None:
    """The id of the camera whose id or label is `asked` (any case), or None."""
    wanted = asked.strip().casefold()
    for camera in cameras:
        if camera.id.casefold() == wanted:
            return str(camera.id)
    for camera in cameras:
        if camera.label and camera.label.strip().casefold() == wanted:
            return str(camera.id)
    return None


def d200_preset_routes() -> APIRouter:
    """The router the cameras module mounts at the service root."""
    router = APIRouter()

    def recall(request: Request, camera: str, number: str) -> Response:
        services = services_of(request)
        if not number.isdigit() or int(number) < 1:
            return respond({"error": BAD_NUMBER}, 400)
        settings = services.settings.get(MODULE)
        camera_id = camera_id_for(list(getattr(settings, "cameras", [])), camera)
        if camera_id is None:
            return respond({"error": f"no camera called {camera!r}"}, 404)
        try:
            services.registry.dispatch_command(
                MODULE, "recall_preset", {"camera": camera_id, "n": int(number) - 1}, D200_SOURCE
            )
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        return respond({"ok": True, "camera": camera_id, "preset": int(number)})

    router.add_api_route(
        "/action/preset/{camera}/{number}",
        recall,
        methods=["GET", "POST"],
        include_in_schema=False,
    )
    return router
