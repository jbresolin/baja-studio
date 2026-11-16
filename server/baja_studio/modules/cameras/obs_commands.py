"""Camera setup's OBS helpers: sources, Test connection, Create filter (ADRs 0020, 0021).

- `list_obs_sources {obs?, camera?}`: the inputs of an OBS, for the source
  picker. Without `obs`, playout's OBS (as before). With `obs` (the form's
  `crop.obs`: remote, host, port, password), that OBS: through its open
  connection when a camera already uses it, else a short connection of its
  own (`remote_obs_probe.py`).
- `test_obs_connection {obs, camera?}`: connect to that remote OBS with the
  form's address and password and say connected / unreachable / wrong
  password, with OBS's version.
- `create_crop_filter {camera}`: create the camera's Crop/Pad filter in the
  camera's own OBS. Creating a filter is always this explicit command, never
  something a driver does by itself (ADR 0020).

A password the form sends back masked (`********`, the way the settings API
shows secrets) means "the saved one": the saved password of `camera`.
Commands with a password in their arguments are never written to the
command log (`UNLOGGED_OBS_COMMANDS`), and no answer or error repeats it.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_transport import ObsError, ObsRequestFailedError, TransportFactory
from baja_studio.cameras.config import CameraConfig, CamerasSettings, DriverKind
from baja_studio.cameras.crop_config import ObsConnection
from baja_studio.cameras.drivers.obs_crop_sync import CROP_FILTER_KIND, NEW_FILTER_SETTINGS
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.cameras.remote_obs import RemoteObsPool
from baja_studio.cameras.remote_obs_probe import ObsProbeError, probe_obs, temporary_client
from baja_studio.core.clock import Clock
from baja_studio.core.module import CommandError
from baja_studio.core.settings import SECRET_MASK

OBS_COMMANDS = frozenset({"list_obs_sources", "create_crop_filter", "test_obs_connection"})
# Reads, and commands whose arguments hold a password: never in the command log.
UNLOGGED_OBS_COMMANDS = frozenset({"list_obs_sources", "test_obs_connection"})


class ObsCameraCommands:
    """The cameras module's commands that talk to OBS directly (not through a driver)."""

    def __init__(
        self,
        pool: RemoteObsPool,
        settings: Callable[[], CamerasSettings],
        clock: Clock,
        transport_factory: TransportFactory,
    ) -> None:
        """`pool` gives each camera its OBS (playout's or a remote one); `settings` the
        cameras module's current ones; `transport_factory` opens Camera setup's own
        short connections to a remote OBS."""
        self._pool = pool
        self._settings = settings
        self._clock = clock
        self._transport_factory = transport_factory

    def handle(self, command: str, args: dict[str, Any]) -> Any:
        """Run one of `OBS_COMMANDS`; refusals are `CommandError`s for the operator."""
        camera_id = str(args.get("camera") or "")
        if command == "list_obs_sources":
            return self._list_sources(self._connection(args.get("obs"), camera_id))
        if command == "test_obs_connection":
            return self._test_connection(self._connection(args.get("obs"), camera_id))
        return self._create_crop_filter(camera_id)

    # --- list_obs_sources and test_obs_connection ------------------------------------------

    def _connection(self, obs: Any, camera_id: str) -> ObsConnection:
        """The `obs` argument as settings (playout's OBS when absent), password unmasked."""
        if obs is None:
            return ObsConnection()
        if not isinstance(obs, dict):
            raise CommandError("obs must be an object like the camera's crop.obs settings")
        try:
            connection = ObsConnection.model_validate(obs)
        except ValidationError as invalid:
            # Only where and what, never the values: one of them may be the password.
            first = invalid.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "obs"
            raise CommandError(f"obs: {where}: {first['msg']}") from None
        if connection.password == SECRET_MASK:
            saved = self._saved_camera(camera_id)
            password = saved.crop.obs.password if saved is not None else ""
            connection = connection.model_copy(update={"password": password})
        return connection

    def _list_sources(self, connection: ObsConnection) -> dict[str, Any]:
        """The OBS's inputs, or `connected: false` with the reason (never an error)."""
        gateway = self._pool.gateway_for(connection)
        link, client = gateway.link(), gateway.client()
        if client is not None and link.connected:
            try:
                return _source_list(client.get_input_list())
            except ObsError as error:
                return _not_connected(link.refused(error))
        if not connection.remote:
            return _not_connected(link.reason)
        # A remote OBS no running camera uses yet: ask it over a connection of our own.
        try:
            with temporary_client(
                connection.host,
                connection.port,
                connection.password,
                self._clock,
                self._transport_factory,
            ) as temporary:
                return _source_list(temporary.get_input_list())
        except ObsProbeError as failed:
            return _not_connected(str(failed))
        except ObsError as error:
            return _not_connected(f"OBS: {error}")

    def _test_connection(self, connection: ObsConnection) -> dict[str, str]:
        """Connected / unreachable / wrong password, for the address and password given."""
        if not connection.remote:
            link = self._pool.local.link()
            if link.connected:
                return {"result": "connected", "message": "playout's OBS is connected"}
            return {"result": "unreachable", "message": link.problem}
        result = probe_obs(
            connection.host,
            connection.port,
            connection.password,
            self._clock,
            self._transport_factory,
        )
        return result.as_reply()

    # --- create_crop_filter ---------------------------------------------------------------

    def _create_crop_filter(self, camera_id: str) -> dict[str, Any]:
        camera = self._crop_camera(camera_id)
        source, filter_name = camera.obs_source or "", camera.crop.filter_name
        gateway = self._pool.gateway_for(camera.crop.obs)
        link, client = gateway.link(), gateway.client()
        if client is None or not link.connected:
            raise CommandError(_offline(gateway))
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
            raise CommandError(link.refused(error)) from None
        return {"created": filter_name, "source": source}

    def _crop_camera(self, camera_id: str) -> CameraConfig:
        camera = self._saved_camera(camera_id)
        if camera is None:
            raise CommandError(f"no camera called {camera_id!r} (save it first)")
        if camera.driver != DriverKind.OBS_CROP:
            raise CommandError(f"camera {camera_id} is not an OBS crop camera")
        return camera

    def _saved_camera(self, camera_id: str) -> CameraConfig | None:
        return next((c for c in self._settings().cameras if c.id == camera_id), None)


def _offline(gateway: ObsGateway) -> str:
    """Why a command cannot reach the OBS: playout's words as before, or the remote's."""
    link = gateway.link()
    return link.problem if link.where else f"OBS is offline: {link.reason}"


def _source_list(inputs: list[Any]) -> dict[str, Any]:
    return {
        "connected": True,
        "reason": "",
        "sources": [{"name": item.name, "kind": item.kind} for item in inputs],
    }


def _not_connected(reason: str) -> dict[str, Any]:
    return {"connected": False, "reason": reason, "sources": []}
