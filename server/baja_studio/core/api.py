"""HTTP and WebSocket endpoints shared by every module.

GET  /api/health                     health of every module
GET  /api/settings                   list of settings sections
GET  /api/settings/{section}         values (secrets masked) + version
PUT  /api/settings/{section}         save {version, values}; 409 on a stale version
GET  /api/settings/{section}/schema  JSON Schema used to draw the Settings form
GET  /api/commands?module=&limit=    command log tail
POST /api/commands                   run a command from another program
GET  /api/logs?limit=                recent log entries
GET  /api/logs/stream                live log entries (Server-Sent Events)
WS   /ws                             state snapshot + patches; commands in, replies out
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from baja_studio.core.commandlog import api_source, web_source
from baja_studio.core.logging import stream_log_events
from baja_studio.core.module import CommandError
from baja_studio.core.registry import UnknownModuleError
from baja_studio.core.services import SYSTEM_SETTINGS_SECTION, StudioServices
from baja_studio.core.settings import SettingsConflictError, UnknownSettingsSectionError
from baja_studio.core.supervisor import HealthState

logger = logging.getLogger(__name__)

router = APIRouter()


def services_of(request: Request | WebSocket) -> StudioServices:
    """The shared services stored on the app at startup."""
    services: StudioServices = request.app.state.services
    return services


class SettingsSaveRequest(BaseModel):
    """Body of `PUT /api/settings/{section}`."""

    version: int | None = Field(
        default=None, description="Version the edit was based on; omit to force the save."
    )
    values: dict[str, Any]


class CommandRequest(BaseModel):
    """Body of `POST /api/commands`."""

    module: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    caller: str = "anonymous"


@router.get("/api/health")
def get_health(request: Request) -> dict[str, Any]:
    """Each module's health; `status` is "ok" only if no running module has a problem."""
    services = services_of(request)
    modules = []
    for module in services.registry.modules():
        health = services.supervisor.module_health(module.name)
        modules.append(
            {
                "name": module.name,
                "title": module.title,
                "state": health.state.value,
                "message": health.message,
            }
        )
    problems = [m for m in modules if m["state"] in (HealthState.DEGRADED, HealthState.OFFLINE)]
    return {"status": "ok" if not problems else "degraded", "modules": modules}


@router.get("/api/settings")
def list_settings_sections(request: Request) -> list[dict[str, str]]:
    """Every section the Settings page can edit: one per module, plus "system"."""
    services = services_of(request)
    titles = {module.name: module.title for module in services.registry.modules()}
    titles[SYSTEM_SETTINGS_SECTION] = "System"
    return [
        {"section": section, "title": titles.get(section, section)}
        for section in services.settings.sections()
    ]


@router.get("/api/settings/{section}")
def get_settings(section: str, request: Request) -> dict[str, Any]:
    """Current values of one section, with secrets masked."""
    settings = services_of(request).settings
    try:
        record = settings.record(section)
        return {
            "section": section,
            "version": record.version,
            "updated_at": record.updated_at,
            "values": settings.masked_values(section),
        }
    except UnknownSettingsSectionError:
        raise HTTPException(404, f"no settings section {section!r}") from None


@router.put("/api/settings/{section}")
def save_settings(section: str, body: SettingsSaveRequest, request: Request) -> dict[str, Any]:
    """Validate and save; 409 if someone else saved first, 422 if values are invalid."""
    settings = services_of(request).settings
    try:
        settings.update(section, body.values, expected_version=body.version)
    except UnknownSettingsSectionError:
        raise HTTPException(404, f"no settings section {section!r}") from None
    except SettingsConflictError as conflict:
        raise HTTPException(
            409, {"message": str(conflict), "current_version": conflict.current_version}
        ) from None
    except ValidationError as invalid:
        raise HTTPException(422, json.loads(invalid.json(include_url=False))) from None
    return get_settings(section, request)


@router.get("/api/settings/{section}/schema")
def get_settings_schema(section: str, request: Request) -> dict[str, Any]:
    """JSON Schema of the section's settings model."""
    try:
        return services_of(request).settings.schema(section)
    except UnknownSettingsSectionError:
        raise HTTPException(404, f"no settings section {section!r}") from None


@router.get("/api/commands")
def get_command_log(
    request: Request, module: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Newest commands first, optionally only one module's."""
    return services_of(request).command_log.tail(module=module, limit=max(1, min(limit, 1000)))


@router.post("/api/commands")
def post_command(body: CommandRequest, request: Request) -> dict[str, Any]:
    """Run a command on behalf of another program (source `api:<caller>`)."""
    registry = services_of(request).registry
    try:
        result = registry.dispatch_command(
            body.module, body.command, body.args, api_source(body.caller)
        )
    except UnknownModuleError:
        raise HTTPException(404, f"no module {body.module!r}") from None
    except CommandError as refused:
        raise HTTPException(400, str(refused)) from None
    return {"ok": True, "result": result}


@router.get("/api/logs")
def get_recent_logs(request: Request, limit: int = 200) -> list[dict[str, Any]]:
    """The most recent log entries, oldest first."""
    return services_of(request).log_hub.recent(max(1, min(limit, 500)))


@router.get("/api/logs/stream")
async def stream_logs(request: Request) -> StreamingResponse:
    """Server-Sent Events: recent entries, then each new one as it is logged."""
    events = stream_log_events(services_of(request).log_hub, request.is_disconnected)
    return StreamingResponse(
        events, media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.websocket("/ws")
async def state_socket(websocket: WebSocket) -> None:
    """One browser's connection: send state, receive commands, send replies.

    All outgoing messages go through the client's queue, drained by a single
    sender task, so patches and replies never interleave mid-message.
    """
    services = services_of(websocket)
    await websocket.accept()
    client_id = uuid.uuid4().hex[:8]
    channel = services.state.add_client(client_id)
    command_tasks: set[asyncio.Task[None]] = set()

    async def send_queued_messages() -> None:
        while True:
            message = await channel.queue.get()
            await websocket.send_json(message)

    async def run_command(message: dict[str, Any]) -> None:
        reply = await run_in_threadpool(handle_socket_command, services, client_id, message)
        channel.queue.put_nowait(reply)

    sender = asyncio.create_task(send_queued_messages())
    try:
        while True:
            text = await websocket.receive_text()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "snapshot_request":
                services.state.send_snapshot(client_id)
                continue
            # Each command runs as its own task so a slow one doesn't hold up the socket.
            task = asyncio.create_task(run_command(message))
            command_tasks.add(task)
            task.add_done_callback(command_tasks.discard)
    except WebSocketDisconnect:
        pass
    finally:
        services.state.remove_client(client_id)
        sender.cancel()
        for task in command_tasks:
            task.cancel()


def handle_socket_command(
    services: StudioServices, client_id: str, message: dict[str, Any]
) -> dict[str, Any]:
    """Run one `{module, command, args, request_id}` message; return the reply message."""
    request_id = message.get("request_id")
    try:
        result = services.registry.dispatch_command(
            str(message.get("module", "")),
            str(message.get("command", "")),
            message.get("args") or {},
            web_source(client_id),
        )
    except UnknownModuleError:
        error = f"no module {message.get('module')!r}"
    except CommandError as refused:
        error = str(refused)
    except Exception as unexpected:
        logger.exception("command failed", extra={"studio_module": message.get("module")})
        error = f"{type(unexpected).__name__}: {unexpected}"
    else:
        return {"type": "reply", "request_id": request_id, "ok": True, "result": result}
    return {"type": "reply", "request_id": request_id, "ok": False, "error": error}
