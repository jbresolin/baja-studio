"""A stand-in for the service's HTTP API, plugged into `StudioClient` as its `fetch`.

It answers `/api/health`, `POST /api/commands` (diagnostics bundle) and the
bundle download from plain attributes a test sets, and records each request.
Setting `answering = False` makes every request fail like a stopped service.
"""

import json
import urllib.error
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

from baja_studio_tray.service import StudioClient

BASE_URL = "http://studio.test"
BUNDLE_NAME = "BajaStudio-diag-20261109-120000.zip"
BUNDLE_URL = f"/api/home/diagnostics/{BUNDLE_NAME}"


def health_answer(status: str = "ok", **module_states: str) -> dict[str, Any]:
    """A `/api/health` answer; `playout="offline"` adds that module in that state."""
    modules = [
        {"name": name, "title": name.capitalize(), "state": state, "message": ""}
        for name, state in module_states.items()
    ]
    return {"status": status, "version": "0.2.0", "modules": modules}


@dataclass
class FakeStudio:
    """The service, as the tray sees it."""

    health: dict[str, Any] = field(default_factory=health_answer)
    answering: bool = True
    bundle_status: int = 200
    bundle_content: bytes = b"PK fake zip"
    requests: list[tuple[str, str, dict[str, Any] | None, float]] = field(default_factory=list)

    def fetch(self, method: str, url: str, body: dict[str, Any] | None, timeout: float) -> bytes:
        self.requests.append((method, url, body, timeout))
        if not self.answering:
            raise ConnectionRefusedError("connection refused")
        path = url.removeprefix(BASE_URL)
        if method == "GET" and path == "/api/health":
            return json.dumps(self.health).encode()
        if method == "POST" and path == "/api/commands":
            if self.bundle_status != 200:
                raise urllib.error.HTTPError(url, self.bundle_status, "error", Message(), None)
            result = {"file_name": BUNDLE_NAME, "url": BUNDLE_URL}
            return json.dumps({"ok": True, "result": result}).encode()
        if method == "GET" and path == BUNDLE_URL:
            return self.bundle_content
        raise urllib.error.HTTPError(url, 404, "not found", Message(), None)

    def client(self) -> StudioClient:
        return StudioClient(BASE_URL, self.fetch)
