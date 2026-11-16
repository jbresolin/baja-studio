"""The camera node's web server: the studio's app with one module, the node's (ADR 0023).

`create_node_app` is the studio's own `create_app` (settings store with
masked secrets, supervisor, logging, state, command log, the core API) with
the node module as its only module, plus what the agent contract needs:

- the contract's error rules on its paths only (`/agent/...`, `/cameras/...`):
  `{error, detail}`, and `400 bad_request` for a malformed body; the core
  API under `/api` keeps the studio's own answers;
- the node's token (`node.token`): with one set, every contract path but
  `GET /agent/info` needs `X-Agent-Token` (contract §2.2), and so does the
  node's own page: the core API under `/api` (settings, commands, logs) and
  the page's state socket `/ws`, which a browser opens with `?token=` (it
  cannot set headers on a WebSocket). Without a token everything is open,
  as on a fresh install, and the page says so.

The page is the node's own small frontend build (`frontend/dist-node`).
"""

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from baja_studio.agents.contract import TOKEN_HEADER
from baja_studio.agents.server import add_contract_error_handlers, token_matches, token_refusal
from baja_studio.core.clock import Clock
from baja_studio.main import create_app
from baja_studio.node.agent_routes import is_contract_path
from baja_studio.node.module import NodeModule
from baja_studio.node.parts import NodeParts
from baja_studio.node.settings import NodeSettings

NODE_DATA_ENVIRONMENT_VARIABLE = "BAJA_NODE_DATA"
FRONTEND_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_FRONTEND"
STATE_SOCKET_PATH = "/ws"
# Close code for a page's state socket refused for its token (as for the agent's, 4401).
REFUSED_SOCKET_CODE = 4401


def default_node_data_directory() -> Path:
    """Where the node keeps its database and logs (`BAJA_NODE_DATA` overrides)."""
    override = os.environ.get(NODE_DATA_ENVIRONMENT_VARIABLE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "BajaCameraNode"
    return Path.home() / ".local" / "share" / "baja-camera-node"


def default_node_frontend_directory() -> Path:
    """The node's page: `frontend/dist-node` next to `server/`, unless the installer says."""
    override = os.environ.get(FRONTEND_ENVIRONMENT_VARIABLE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "frontend" / "dist-node"


def create_node_app(
    parts: NodeParts,
    data_directory: Path | None = None,
    clock: Clock | None = None,
    frontend_directory: Path | None = None,
    log_to_file: bool = True,
    token_override: str | None = None,
) -> FastAPI:
    """The node's app; its module starts when the app starts serving.

    `token_override` (tests, and the rig's `--token`) sets the node's token
    as if it had been typed on its page (it is saved).
    """
    app = create_app(
        data_directory=data_directory or default_node_data_directory(),
        clock=clock,
        frontend_directory=frontend_directory or default_node_frontend_directory(),
        log_to_file=log_to_file,
        module_list_builder=lambda registry: [NodeModule(parts)],
    )
    services = app.state.services
    if token_override is not None:
        services.settings.update("node", {"token": token_override})

    def current_token() -> str:
        settings = services.settings.get("node")
        assert isinstance(settings, NodeSettings)
        return settings.token

    add_contract_error_handlers(app, applies_to=is_contract_path)

    @app.middleware("http")
    async def check_token(request: Request, call_next: Any) -> Response:
        path = request.url.path
        if is_contract_path(path) or path.startswith("/api/"):
            refusal = token_refusal(path, request.headers.get(TOKEN_HEADER), current_token())
            if refusal is not None:
                return refusal
        response: Response = await call_next(request)
        return response

    app.add_middleware(_StateSocketToken, token=current_token)
    return app


class _StateSocketToken:
    """Refuses the page's state socket (`/ws`) without `?token=` when the node has a token."""

    def __init__(self, app: ASGIApp, token: Any) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket" and scope.get("path") == STATE_SOCKET_PATH:
            token = self._token()
            query = parse_qs(scope.get("query_string", b"").decode())
            sent = (query.get("token") or [""])[0]
            if token and not token_matches(sent, token):
                await send({"type": "websocket.close", "code": REFUSED_SOCKET_CODE})
                return
        await self.app(scope, receive, send)
