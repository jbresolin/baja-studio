"""Which OBS each crop camera talks to: playout's, or a shared remote connection (ADR 0021).

An `obs_crop` camera's settings say where its source is (`crop.obs`): in
playout's OBS on this PC, or in the OBS of a trackside laptop at host:port.
The registry asks this pool for a camera's `ObsGateway` when it builds the
camera's driver (`lease`) and gives it back when the driver is gone
(`release`):

- playout's OBS: playout's own gateway, as before (nothing is opened here);
- a remote OBS: **one connection per distinct host and port**, shared by
  every camera that names it (a laptop with two 4K webcams has two crop
  cameras on one connection). It is opened when the first camera needs it
  and closed when the last one leaves (removed, disabled, or moved to another
  address); its worker reconnects by itself (`remote_obs_connection.py`).

**Passwords.** Two cameras naming the same OBS could be saved with
different passwords; one connection can only use one. The camera saved last
wins: at each settings change, a camera whose password differs from what it
had before (or a camera that is new) gives its password to its connection.
The others get a note for Camera setup saying whose password is used. The
password itself is never published, logged or put in a note.

Everything here is quick and never touches the network on the caller's
thread, except `release`, which waits for the last camera's connection to
close (outside any lock, like stopping a driver).
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_transport import TransportFactory, open_websocket_transport
from baja_studio.cameras.config import CameraConfig, DriverKind
from baja_studio.cameras.crop_config import ObsConnection
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsGateway
from baja_studio.cameras.remote_obs_connection import Logger, RemoteObsConnection, where_of
from baja_studio.core.clock import Clock
from baja_studio.core.module import ModuleContext
from baja_studio.core.supervisor import WorkerSpec

ConnectionKey = tuple[str, int]  # (host in lower case, port)


def remote_key(camera: CameraConfig) -> ConnectionKey | None:
    """The remote OBS an `obs_crop` camera uses, or None (playout's OBS, or another driver)."""
    return camera.crop.obs.key() if camera.driver == DriverKind.OBS_CROP else None


class RemoteObsPool:
    """Hands each crop camera its OBS gateway; one remote connection per host and port."""

    def __init__(
        self,
        local: ObsGateway,
        clock: Clock,
        transport_factory: TransportFactory,
        start_worker: Callable[[WorkerSpec], None],
        stop_worker: Callable[[str], None],
        logger: Logger,
    ) -> None:
        """`local` is playout's gateway; connections run as workers started and stopped
        through `start_worker` / `stop_worker` (the cameras module's)."""
        self._local = local
        self._clock = clock
        self._transport_factory = transport_factory
        self._start_worker = start_worker
        self._stop_worker = stop_worker
        self._logger = logger
        self._lock = threading.Lock()
        self._connections: dict[ConnectionKey, RemoteObsConnection] = {}
        self._leases: dict[str, ConnectionKey] = {}  # camera id → its remote OBS
        # "Saved last wins": the password each camera had at the last settings change,
        # which camera's password each remote OBS uses, and that password.
        self._passwords_seen: dict[str, str] = {}
        self._password_owner: dict[ConnectionKey, str] = {}
        self._passwords: dict[ConnectionKey, str] = {}
        self._notes: dict[str, str] = {}

    @classmethod
    def for_module(
        cls,
        context: ModuleContext,
        local: ObsGateway,
        transport_factory: TransportFactory = open_websocket_transport,
    ) -> "RemoteObsPool":
        """A pool whose connections are workers of the module running in `context`."""
        return cls(
            local,
            context.clock,
            transport_factory,
            context.start_worker,
            context.stop_worker,
            context.logger,
        )

    @property
    def local(self) -> ObsGateway:
        """Playout's OBS (on this PC)."""
        return self._local

    def choose_passwords(self, cameras: list[CameraConfig]) -> None:
        """Decide each remote OBS's password from the new settings (the camera saved last wins).

        Called by the registry on every settings change, before it builds
        drivers; an open connection whose password changed reconnects with it.
        """
        remote = [camera for camera in cameras if camera.enabled and remote_key(camera)]
        by_key: dict[ConnectionKey, list[CameraConfig]] = {}
        with self._lock:
            for camera in remote:
                key = remote_key(camera)
                assert key is not None
                by_key.setdefault(key, []).append(camera)
                if self._passwords_seen.get(camera.id) != camera.crop.obs.password:
                    self._password_owner[key] = camera.id  # saved (or added) now: it wins
            for key, members in by_key.items():
                if self._password_owner.get(key) not in {camera.id for camera in members}:
                    self._password_owner[key] = members[-1].id  # the owner left this OBS
            self._password_owner = {key: self._password_owner[key] for key in by_key}
            owners = {camera.id: camera for camera in remote}
            self._passwords = {
                key: owners[owner].crop.obs.password for key, owner in self._password_owner.items()
            }
            self._passwords_seen = {camera.id: camera.crop.obs.password for camera in remote}
            self._notes = {
                camera.id: self._conflict_note(camera, by_key[key], self._password_owner[key])
                for key, members in by_key.items()
                for camera in members
            }
            connections = [
                (connection, self._passwords[key])
                for key, connection in self._connections.items()
                if key in self._passwords
            ]
        for connection, password in connections:
            connection.set_password(password)

    def describe(self, camera: CameraConfig) -> dict[str, Any]:
        """A crop camera's OBS as its state shows it (`state.cameras.cameras.<id>.obs`).

        `address` is None for playout's OBS; `note` is what Camera setup should
        say about the connection ("" if nothing). No password, ever.
        """
        obs = camera.crop.obs
        with self._lock:
            note = self._notes.get(camera.id, "")
        address = where_of(obs.host, obs.port) if obs.remote else None
        return {"remote": obs.remote, "address": address, "note": note}

    def lease(self, camera_id: str, connection: ObsConnection) -> ObsGateway:
        """The gateway for a camera's driver; opens the remote connection if it is the first.

        Each lease is given back with `release(camera_id)` once the driver is stopped.
        """
        key = connection.key()
        with self._lock:
            leased = self._leases.get(camera_id)
        if leased is not None and leased != key:
            self.release(camera_id)  # (the registry releases first; this is a safety net)
        if key is None:
            return self._local
        with self._lock:
            self._leases[camera_id] = key
            existing = self._connections.get(key)
            if existing is not None:
                return existing
            opened = RemoteObsConnection(
                connection.host,
                connection.port,
                self._passwords.get(key, connection.password),
                self._clock,
                self._transport_factory,
                self._logger,
            )
            self._connections[key] = opened
        self._start_worker(opened.worker_spec())
        return opened

    def release(self, camera_id: str) -> None:
        """A camera's driver is gone; close its remote connection if no other camera uses it."""
        with self._lock:
            key = self._leases.pop(camera_id, None)
            if key is None or key in self._leases.values():
                return
            closing = self._connections.pop(key)
        # Outside the lock: stopping waits for the worker, which closes its client.
        self._stop_worker(closing.worker_name)

    def gateway_for(self, connection: ObsConnection) -> ObsGateway:
        """The gateway a preview or a Camera setup command uses for this OBS, without a lease.

        A remote OBS no running camera uses has no connection: its gateway says so.
        """
        key = connection.key()
        if key is None:
            return self._local
        with self._lock:
            existing = self._connections.get(key)
        if existing is not None:
            return existing
        where = where_of(connection.host, connection.port)
        return NoObsGateway(f"OBS at {where}: no enabled camera uses it yet (save the camera)")

    def connection(self, host: str, port: int) -> RemoteObsConnection | None:
        """The open connection to host:port, if a camera uses it."""
        with self._lock:
            return self._connections.get((host.strip().lower(), port))

    def password(self, host: str, port: int) -> str | None:
        """The password the connection to host:port uses (for Test connection), if any."""
        with self._lock:
            return self._passwords.get((host.strip().lower(), port))

    def open_connections(self) -> list[RemoteObsConnection]:
        """Every open remote connection (tests wait for their requests to be answered)."""
        with self._lock:
            return list(self._connections.values())

    @staticmethod
    def _conflict_note(camera: CameraConfig, members: list[CameraConfig], owner: str) -> str:
        """Said on Camera setup when this camera's password is not the one in use."""
        chosen = next(member for member in members if member.id == owner)
        if camera.id == owner or camera.crop.obs.password == chosen.crop.obs.password:
            return ""
        where = where_of(camera.crop.obs.host, camera.crop.obs.port)
        return (
            f"camera '{owner}' was saved later with another password for OBS at {where}, "
            "and the connection uses that one: save the same password on both cameras"
        )
