"""A stand-in for the camera node on this laptop, plugged into `NodeClient` as its `fetch`.

The real node answers `GET /api/node/tray-status` with its status, version
and one entry per camera (server/baja_studio/node/tray_status.py). This fake
answers the same from plain attributes a test sets, and records each request.
Setting `answering = False` makes every request fail like a stopped service.
"""

import json
import urllib.error
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

from baja_studio_tray.service import NODE_TRAY_STATUS_PATH, NodeClient


def node_camera(
    camera_id: str = "cam1",
    label: str = "Left S600",
    mode: str = "node",
    status: str = "ok",
    reason: str = "",
) -> dict[str, Any]:
    """One camera as the node lists it: `status` is ok, degraded or offline."""
    return {"id": camera_id, "label": label, "mode": mode, "status": status, "reason": reason}


def node_answer(status: str = "ok", *cameras: dict[str, Any]) -> dict[str, Any]:
    """A `/api/node/tray-status` answer with these cameras (none given: one camera, live)."""
    listed = list(cameras) if cameras else [node_camera()]
    return {"status": status, "version": "0.7.0", "name": "XPS", "cameras": listed}


@dataclass
class FakeNode:
    """The camera node, as its tray sees it."""

    answer: dict[str, Any] = field(default_factory=node_answer)
    answering: bool = True
    # The port the client is pointed at (the node writes it to port.txt; tests set it here).
    port: int = 8891
    requests: list[tuple[str, str, dict[str, Any] | None, float]] = field(default_factory=list)

    def fetch(self, method: str, url: str, body: dict[str, Any] | None, timeout: float) -> bytes:
        self.requests.append((method, url, body, timeout))
        if not self.answering:
            raise ConnectionRefusedError("connection refused")
        if method == "GET" and url == f"http://127.0.0.1:{self.port}{NODE_TRAY_STATUS_PATH}":
            return json.dumps(self.answer).encode()
        raise urllib.error.HTTPError(url, 404, "not found", Message(), None)

    def client(self) -> NodeClient:
        return NodeClient(lambda: self.port, self.fetch)
