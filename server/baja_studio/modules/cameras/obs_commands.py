"""Camera setup's OBS helpers: `list_obs_sources {}` and `create_crop_filter {camera}`.

The setup page picks an `obs_crop` camera's source from OBS's input list
while OBS is connected (free text otherwise), and creates the camera's
Crop/Pad filter when it is missing. Creating a filter is always this explicit
command, never something a driver does by itself (ADR 0020).
"""

from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_transport import ObsError, ObsRequestFailedError
from baja_studio.cameras.config import CameraConfig, CamerasSettings, DriverKind
from baja_studio.cameras.drivers.obs_crop_sync import CROP_FILTER_KIND, NEW_FILTER_SETTINGS
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.core.module import CommandError

OBS_COMMANDS = frozenset({"list_obs_sources", "create_crop_filter"})
# A read: nothing worth finding in the command log after an event.
UNLOGGED_OBS_COMMANDS = frozenset({"list_obs_sources"})


class ObsCameraCommands:
    """The cameras module's commands that talk to OBS directly (not through a driver)."""

    def __init__(self, obs: ObsGateway, settings: Callable[[], CamerasSettings]) -> None:
        """`obs` is playout's OBS connection; `settings` the cameras module's current ones."""
        self._obs = obs
        self._settings = settings

    def handle(self, command: str, args: dict[str, Any]) -> Any:
        """Run one of `OBS_COMMANDS`; refusals are `CommandError`s for the operator."""
        if command == "list_obs_sources":
            return self._list_sources()
        return self._create_crop_filter(str(args.get("camera") or ""))

    def _list_sources(self) -> dict[str, Any]:
        link, client = self._obs.link(), self._obs.client()
        if client is None or not link.connected:
            return {"connected": False, "reason": link.reason, "sources": []}
        try:
            inputs = client.get_input_list()
        except ObsError as error:
            return {"connected": False, "reason": f"OBS: {error}", "sources": []}
        return {
            "connected": True,
            "reason": "",
            "sources": [{"name": item.name, "kind": item.kind} for item in inputs],
        }

    def _create_crop_filter(self, camera_id: str) -> dict[str, Any]:
        camera = self._crop_camera(camera_id)
        source, filter_name = camera.obs_source or "", camera.crop.filter_name
        link, client = self._obs.link(), self._obs.client()
        if client is None or not link.connected:
            raise CommandError(f"OBS is offline: {link.reason}")
        try:
            client.create_source_filter(source, filter_name, CROP_FILTER_KIND, NEW_FILTER_SETTINGS)
        except ObsRequestFailedError as error:
            if error.code == RequestStatus.RESOURCE_ALREADY_EXISTS:
                raise CommandError(
                    f"source '{source}' already has a filter called '{filter_name}'"
                ) from None
            if error.code == RequestStatus.RESOURCE_NOT_FOUND:
                raise CommandError(f"OBS has no source '{source}'") from None
            raise CommandError(f"OBS refused: {error.comment or error}") from None
        except ObsError as error:
            raise CommandError(f"OBS: {error}") from None
        return {"created": filter_name, "source": source}

    def _crop_camera(self, camera_id: str) -> CameraConfig:
        for camera in self._settings().cameras:
            if camera.id == camera_id:
                if camera.driver != DriverKind.OBS_CROP:
                    raise CommandError(f"camera {camera_id} is not an OBS crop camera")
                return camera
        raise CommandError(f"no camera called {camera_id!r} (save it first)")
