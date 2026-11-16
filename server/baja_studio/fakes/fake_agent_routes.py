"""HTTP and WebSocket routes of the fake agent: the contract's, plus `/_fake` switches.

Contract routes (docs/agent-api.md) check `X-Agent-Token` when a token is
set, except `GET /agent/info`, which a subnet scan must be able to read.
Errors are always `{error, detail}` (the rules shared with the camera node
live in `agents/server.py`). Crop cameras serve contract 1.1's
`GET /cameras/{id}/snapshot` (a test card, `blocky_jpeg.py`). The `/_fake`
routes never need a token:

    GET  /_fake                              current switches
    POST /_fake/silence {silent}             stop pushing and answering pings
    POST /_fake/freeze_state {frozen}        stop pushing state; events and pongs still flow
    POST /_fake/drop_websockets              close every state WebSocket now
    POST /_fake/restart                      behave as if the agent had just restarted
    POST /_fake/delay {ms}                   answer every contract request late
    POST /_fake/cameras/{id}/device {online} unplug / re-plug the device
    POST /_fake/cameras/{id}/tracker {tracker: not_ready|ready|tracking}
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any, TypeVar

from fastapi import FastAPI, Path, Query, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field

from baja_studio.agents.contract import (
    DEFAULT_SNAPSHOT_WIDTH,
    MAX_SNAPSHOT_WIDTH,
    MIN_SNAPSHOT_WIDTH,
    SNAPSHOT_MEDIA_TYPE,
    STUDIO_ID_HEADER,
    TOKEN_HEADER,
    AgentHealth,
    AgentInfo,
    CommandReply,
    CropRect,
    ImageControlChange,
    ImageControlValue,
    LogEntry,
    ModeReply,
    ModeRequest,
    MoveRequest,
    Position,
    Preset,
    PresetLabelRequest,
)
from baja_studio.agents.server import (
    add_contract_error_handlers,
    serve_state_socket,
    token_refusal,
)
from baja_studio.fakes.simulated_common import CameraCommandError, FakeTracker
from baja_studio.fakes.simulated_ptz import SimulatedCamera

if TYPE_CHECKING:
    from baja_studio.fakes.fake_agent import FakeAgent

Result = TypeVar("Result")
PresetNumber = Annotated[int, Path(ge=0, le=255)]
SnapshotWidth = Annotated[int, Query(ge=MIN_SNAPSHOT_WIDTH, le=MAX_SNAPSHOT_WIDTH)]


class SilenceSwitch(BaseModel):
    silent: bool


class FreezeSwitch(BaseModel):
    frozen: bool


class DelaySwitch(BaseModel):
    ms: int = Field(ge=0, le=60_000)


class DeviceSwitch(BaseModel):
    online: bool


class TrackerSwitch(BaseModel):
    tracker: FakeTracker


def add_fake_agent_routes(app: FastAPI, agent: "FakeAgent") -> None:
    """Attach every route, the error handlers and the token/delay middleware to `app`."""
    add_contract_error_handlers(app)

    @app.middleware("http")
    async def check_token_and_delay(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/_fake"):
            return await call_next(request)
        if agent.delay_seconds > 0:
            await agent.clock.sleep(agent.delay_seconds)
        refusal = token_refusal(request.url.path, request.headers.get(TOKEN_HEADER), agent.token)
        return refusal if refusal is not None else await call_next(request)

    def run_command(
        request: Request,
        camera_id: str,
        command: str,
        args: dict[str, Any],
        action: Callable[[SimulatedCamera, str], Result],
    ) -> Result:
        """Run a command on a camera, log it (success or not), and send its events."""
        source = f"studio:{request.headers.get(STUDIO_ID_HEADER, 'unknown')}"
        try:
            result = action(agent.camera(camera_id), source)
        except CameraCommandError as refused:
            agent.record_command(camera_id, source, command, args, f"error: {refused.code}")
            raise
        finally:
            agent.send_pending_events()
        agent.record_command(camera_id, source, command, args, "ok")
        return result

    # --- agent level -----------------------------------------------------------------

    @app.get("/agent/info")
    def get_info() -> AgentInfo:
        return agent.info()

    @app.get("/agent/health")
    def get_health() -> AgentHealth:
        return agent.health()

    @app.get("/agent/log")
    def get_log(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[LogEntry]:
        return agent.log_tail(limit)

    @app.websocket("/agent/ws")
    async def state_socket(websocket: WebSocket) -> None:
        await serve_state_socket(
            websocket,
            agent.hub,
            agent.token,
            greeting=agent.greeting,
            answers_pings=lambda: not agent.silent,
        )

    # --- per camera --------------------------------------------------------------------

    @app.post("/cameras/{camera_id}/move")
    def move(camera_id: str, body: MoveRequest, request: Request) -> CommandReply:
        args = body.model_dump()
        return run_command(
            request, camera_id, "move", args, lambda cam, src: cam.move(agent.now(), body, src)
        )

    @app.post("/cameras/{camera_id}/stop")
    def stop(camera_id: str, request: Request) -> CommandReply:
        return run_command(
            request, camera_id, "stop", {}, lambda cam, src: cam.stop(agent.now(), src)
        )

    @app.post("/cameras/{camera_id}/presets/{n}/recall")
    def recall_preset(camera_id: str, n: PresetNumber, request: Request) -> CommandReply:
        return run_command(
            request,
            camera_id,
            "recall_preset",
            {"n": n},
            lambda cam, src: cam.recall_preset(agent.now(), n, src),
        )

    @app.post("/cameras/{camera_id}/presets/{n}/store")
    def store_preset(camera_id: str, n: PresetNumber, request: Request) -> Preset:
        return run_command(
            request,
            camera_id,
            "store_preset",
            {"n": n},
            lambda cam, src: cam.store_preset(agent.now(), n),
        )

    @app.put("/cameras/{camera_id}/presets/{n}")
    def label_preset(
        camera_id: str, n: PresetNumber, body: PresetLabelRequest, request: Request
    ) -> Preset:
        args = {"n": n, "label": body.label}
        return run_command(
            request,
            camera_id,
            "label_preset",
            args,
            lambda cam, src: cam.label_preset(n, body.label),
        )

    @app.get("/cameras/{camera_id}/position")
    def get_position(camera_id: str) -> Position | None:
        return agent.camera(camera_id).position(agent.now())

    @app.get("/cameras/{camera_id}/mode")
    def get_mode(camera_id: str) -> ModeReply:
        return agent.camera(camera_id).mode_reply()

    @app.put("/cameras/{camera_id}/mode")
    def set_mode(camera_id: str, body: ModeRequest, request: Request) -> ModeReply:
        args = body.model_dump(mode="json", exclude_none=True)
        return run_command(
            request, camera_id, "set_mode", args, lambda cam, src: cam.set_mode(body)
        )

    @app.get("/cameras/{camera_id}/image")
    def get_image(camera_id: str) -> dict[str, ImageControlValue]:
        return agent.camera(camera_id).image_values()

    @app.put("/cameras/{camera_id}/image")
    def set_image(
        camera_id: str, body: dict[str, ImageControlChange], request: Request
    ) -> dict[str, ImageControlValue]:
        args = {name: change.model_dump(exclude_none=True) for name, change in body.items()}
        return run_command(
            request, camera_id, "set_image", args, lambda cam, src: cam.set_image(body)
        )

    @app.get("/cameras/{camera_id}/crop")
    def get_crop(camera_id: str) -> CropRect:
        return agent.camera(camera_id).crop(agent.now())

    @app.put("/cameras/{camera_id}/crop")
    def set_crop(camera_id: str, body: CropRect, request: Request) -> CropRect:
        return run_command(
            request,
            camera_id,
            "set_crop",
            body.model_dump(),
            lambda cam, src: cam.set_crop(agent.now(), body, src),
        )

    @app.get("/cameras/{camera_id}/snapshot")
    def get_snapshot(camera_id: str, width: SnapshotWidth = DEFAULT_SNAPSHOT_WIDTH) -> Response:
        picture = agent.camera(camera_id).snapshot(agent.now(), width)
        return Response(
            picture, media_type=SNAPSHOT_MEDIA_TYPE, headers={"Cache-Control": "no-store"}
        )

    @app.post("/cameras/{camera_id}/resync")
    def resync(camera_id: str, request: Request) -> CommandReply:
        return run_command(
            request, camera_id, "resync", {}, lambda cam, src: cam.resync(agent.now())
        )

    # --- failure switches ------------------------------------------------------------

    @app.get("/_fake")
    def get_switches() -> dict[str, Any]:
        return {
            "silent": agent.silent,
            "state_frozen": agent.state_frozen,
            "delay_ms": round(agent.delay_seconds * 1000),
        }

    @app.post("/_fake/silence")
    def set_silence(body: SilenceSwitch) -> dict[str, Any]:
        agent.silent = body.silent
        return get_switches()

    @app.post("/_fake/freeze_state")
    def freeze_state(body: FreezeSwitch) -> dict[str, Any]:
        agent.state_frozen = body.frozen
        return get_switches()

    @app.post("/_fake/restart")
    def restart() -> dict[str, Any]:
        agent.restart()
        return get_switches()

    @app.post("/_fake/drop_websockets")
    def drop_websockets() -> dict[str, Any]:
        agent.drop_websockets()
        return get_switches()

    @app.post("/_fake/delay")
    def set_delay(body: DelaySwitch) -> dict[str, Any]:
        agent.delay_seconds = body.ms / 1000
        return get_switches()

    @app.post("/_fake/cameras/{camera_id}/device")
    def set_device(camera_id: str, body: DeviceSwitch) -> dict[str, Any]:
        agent.camera(camera_id).set_online(agent.now(), body.online)
        agent.send_pending_events()
        return {"online": body.online}

    @app.post("/_fake/cameras/{camera_id}/tracker")
    def set_tracker(camera_id: str, body: TrackerSwitch) -> dict[str, Any]:
        agent.camera(camera_id).set_tracker(body.tracker)
        agent.send_pending_events()
        return {"tracker": body.tracker}
