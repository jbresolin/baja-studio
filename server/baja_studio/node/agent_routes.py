"""The node's agent API routes: contract 1.1 (docs/agent-api.md), at the root of the node's server.

Mounted by the node module as its `root_routes`: the contract's paths are
fixed by the studio's client and the MV app, like the D200's are. The token
check and the `{error, detail}` answers for these paths are the node app's
(`app.py`, with `agents/server.py`). Every command is logged in the agent's
own command log, refused ones included, and its events go out at once.
"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Path, Query, Request, WebSocket
from fastapi.responses import Response

from baja_studio.agents.contract import (
    DEFAULT_SNAPSHOT_WIDTH,
    MAX_SNAPSHOT_WIDTH,
    MIN_SNAPSHOT_WIDTH,
    STUDIO_ID_HEADER,
    AgentHealth,
    AgentInfo,
    CommandReply,
    CropRect,
    ErrorCode,
    ImageControlChange,
    ImageControlValue,
    LogEntry,
    ModeReply,
    ModeRequest,
    MoveRequest,
    Position,
    PresetLabelRequest,
)
from baja_studio.agents.server import CameraCommandError, serve_state_socket
from baja_studio.node.agent import NodeAgent
from baja_studio.node.camera import NodeCamera
from baja_studio.node.image_controls import NodeImageControls
from baja_studio.node.settings import NodeCameraSettings

Result = TypeVar("Result")
PresetNumber = Annotated[int, Path(ge=0, le=255)]
SnapshotWidth = Annotated[int, Query(ge=MIN_SNAPSHOT_WIDTH, le=MAX_SNAPSHOT_WIDTH)]
# The paths these routes own: the node app applies the contract's rules to them.
CONTRACT_PREFIXES = ("/agent", "/cameras")


def is_contract_path(path: str) -> bool:
    """True for `/agent/...` and `/cameras/...`."""
    return any(path == prefix or path.startswith(prefix + "/") for prefix in CONTRACT_PREFIXES)


def agent_routes(
    agent: Callable[[], NodeAgent | None],
    image: Callable[[], NodeImageControls | None],
    settings_of: Callable[[str], NodeCameraSettings | None],
) -> APIRouter:
    """The contract's routes; `agent()` and `image()` are None while the module is stopped."""
    router = APIRouter()

    def running() -> NodeAgent:
        current = agent()
        if current is None:
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, "the node is starting or stopping")
        return current

    def run_command(
        request: Request,
        camera_id: str,
        command: str,
        args: dict[str, Any],
        action: Callable[[NodeCamera, str, str], Result],
    ) -> Result:
        """Run a command on a camera, log it (success or not), and send its events."""
        node = running()
        source = f"studio:{request.headers.get(STUDIO_ID_HEADER, 'unknown')}"
        try:
            result = action(node.camera(camera_id), source, node.now_iso())
        except CameraCommandError as refused:
            node.log.record(camera_id, source, command, args, f"error: {refused.code}")
            raise
        finally:
            node.send_pending_events()
        node.log.record(camera_id, source, command, args, "ok")
        return result

    def image_controls() -> NodeImageControls:
        controls = image()
        if controls is None:
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, "the node is starting or stopping")
        return controls

    def camera_settings(camera_id: str) -> NodeCameraSettings:
        running().camera(camera_id)  # 404 unknown_camera first
        found = settings_of(camera_id)
        if found is None:
            raise CameraCommandError(ErrorCode.UNKNOWN_CAMERA, f"no camera {camera_id!r}")
        return found

    def no_presets(camera: NodeCamera, source: str, now: str) -> Any:
        raise CameraCommandError(
            ErrorCode.NOT_SUPPORTED,
            f"{camera.id} is a crop camera: it keeps no presets (the studio stores its windows)",
        )

    # --- agent level --------------------------------------------------------------------

    @router.get("/agent/info")
    def get_info() -> AgentInfo:
        return running().info()

    @router.get("/agent/health")
    def get_health() -> AgentHealth:
        return running().health()

    @router.get("/agent/log")
    def get_log(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[LogEntry]:
        return running().log_tail(limit)

    @router.websocket("/agent/ws")
    async def state_socket(websocket: WebSocket) -> None:
        node = agent()
        if node is None:
            await websocket.close(code=1013)  # try again later
            return
        await serve_state_socket(websocket, node.hub, node.token(), greeting=node.greeting)

    # --- per camera ---------------------------------------------------------------------

    @router.post("/cameras/{camera_id}/move")
    def move(camera_id: str, body: MoveRequest, request: Request) -> CommandReply:
        return run_command(
            request,
            camera_id,
            "move",
            body.model_dump(),
            lambda cam, src, now: cam.move(body, src, now),
        )

    @router.post("/cameras/{camera_id}/stop")
    def stop(camera_id: str, request: Request) -> CommandReply:
        return run_command(request, camera_id, "stop", {}, lambda cam, src, now: cam.stop(src, now))

    @router.post("/cameras/{camera_id}/presets/{n}/recall")
    def recall_preset(camera_id: str, n: PresetNumber, request: Request) -> Any:
        return run_command(request, camera_id, "recall_preset", {"n": n}, no_presets)

    @router.post("/cameras/{camera_id}/presets/{n}/store")
    def store_preset(camera_id: str, n: PresetNumber, request: Request) -> Any:
        return run_command(request, camera_id, "store_preset", {"n": n}, no_presets)

    @router.put("/cameras/{camera_id}/presets/{n}")
    def label_preset(
        camera_id: str, n: PresetNumber, body: PresetLabelRequest, request: Request
    ) -> Any:
        args = {"n": n, "label": body.label}
        return run_command(request, camera_id, "label_preset", args, no_presets)

    @router.get("/cameras/{camera_id}/position")
    def get_position(camera_id: str) -> Position | None:
        return running().camera(camera_id).position()

    @router.get("/cameras/{camera_id}/mode")
    def get_mode(camera_id: str) -> ModeReply:
        return running().camera(camera_id).mode_reply()

    @router.put("/cameras/{camera_id}/mode")
    def set_mode(camera_id: str, body: ModeRequest, request: Request) -> ModeReply:
        args = body.model_dump(mode="json", exclude_none=True)
        return run_command(
            request, camera_id, "set_mode", args, lambda cam, src, now: cam.set_mode(body)
        )

    @router.get("/cameras/{camera_id}/image")
    def get_image(camera_id: str) -> dict[str, ImageControlValue]:
        return image_controls().read(camera_settings(camera_id))

    @router.put("/cameras/{camera_id}/image")
    def set_image(
        camera_id: str, body: dict[str, ImageControlChange], request: Request
    ) -> dict[str, ImageControlValue]:
        args = {name: change.model_dump(exclude_none=True) for name, change in body.items()}
        return run_command(
            request,
            camera_id,
            "set_image",
            args,
            lambda cam, src, now: image_controls().write(camera_settings(camera_id), body),
        )

    @router.get("/cameras/{camera_id}/crop")
    def get_crop(camera_id: str) -> CropRect:
        return running().camera(camera_id).crop()

    @router.put("/cameras/{camera_id}/crop")
    def set_crop(camera_id: str, body: CropRect, request: Request) -> CropRect:
        return run_command(
            request,
            camera_id,
            "set_crop",
            body.model_dump(),
            lambda cam, src, now: cam.set_crop(body, src, now),
        )

    @router.get("/cameras/{camera_id}/snapshot")
    def get_snapshot(camera_id: str, width: SnapshotWidth = DEFAULT_SNAPSHOT_WIDTH) -> Response:
        picture = running().camera(camera_id).snapshot(width)
        return Response(picture.content, media_type=picture.media_type, headers=picture.headers())

    @router.post("/cameras/{camera_id}/resync")
    def resync(camera_id: str, request: Request) -> CommandReply:
        # The node owns its cameras outright; nobody else drove them meanwhile.
        return run_command(
            request,
            camera_id,
            "resync",
            {},
            lambda cam, src, now: CommandReply(accepted=True, mode=cam.mode),
        )

    return router
