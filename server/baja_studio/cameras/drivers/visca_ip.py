"""VISCA over IP driver (the SMTAV, UDP 52381): a `ViscaLink` run in a supervised worker.

The arbiter's calls only record what is wanted and wake the worker; the
worker owns the socket and does all the sending and receiving. The socket is
opened through a `DatagramOpener`, so tests hand it the fake camera in-process
instead of the network.
"""

import contextlib
import socket
import threading
from collections.abc import Callable
from typing import Protocol

from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_link import DEFAULT_POSITION_POLL_SECONDS, ViscaLink
from baja_studio.core.supervisor import DeviceOfflineError, HealthState, WorkerContext, WorkerSpec

RECEIVE_BUFFER_BYTES = 1024
_HEALTH_STATE = {
    Status.OK: HealthState.OK,
    Status.DEGRADED: HealthState.DEGRADED,
    Status.OFFLINE: HealthState.OFFLINE,
}


class DatagramTransport(Protocol):
    """A connected datagram socket that never blocks."""

    def send(self, datagram: bytes) -> None:
        """Send one datagram; a failure counts as a lost packet, so it must not raise."""

    def receive(self) -> bytes | None:
        """The next datagram already received, or None if there is none."""

    def close(self) -> None:
        """Release the socket."""


DatagramOpener = Callable[[str, int], DatagramTransport]


class UdpTransport:
    """The real network: a non-blocking UDP socket connected to the camera."""

    def __init__(self, host: str, port: int) -> None:
        """Open and connect the socket (no packet is sent by connecting UDP)."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        try:
            self._socket.connect((host, port))
        except OSError:
            self._socket.close()
            raise

    def send(self, datagram: bytes) -> None:
        """Send, treating network errors (unreachable, no route) as a lost packet."""
        # The missing reply is what reports the problem.
        with contextlib.suppress(OSError):
            self._socket.send(datagram)

    def receive(self) -> bytes | None:
        """Read one waiting datagram, if any."""
        try:
            return self._socket.recv(RECEIVE_BUFFER_BYTES)
        except (BlockingIOError, InterruptedError):
            return None
        except OSError:
            # Windows reports an earlier ICMP "port unreachable" here (WSAECONNRESET).
            return None

    def close(self) -> None:
        """Close the socket."""
        self._socket.close()


class ViscaIpDriver(CameraDriver):
    """One VISCA/IP camera owned by the studio."""

    def __init__(
        self,
        host: DriverHost,
        address: str,
        port: int,
        resend_interval_seconds: Callable[[], float],
        opener: DatagramOpener = UdpTransport,
        worker_suffix: str = "visca",
        top_speed_steps: Callable[[], tuple[int, int]] = lambda: (
            packets.DEFAULT_PAN_SPEED_MAX_STEP,
            packets.DEFAULT_TILT_SPEED_MAX_STEP,
        ),
        position_poll_seconds: Callable[[], float] = lambda: DEFAULT_POSITION_POLL_SECONDS,
    ) -> None:
        """`resend_interval_seconds`, `top_speed_steps` (pan, tilt) and
        `position_poll_seconds` are read live, so a settings change applies at once."""
        self._host = host
        self._address = address
        self._port = port
        self._resend_interval = resend_interval_seconds
        self._top_speed_steps = top_speed_steps
        self._position_poll_seconds = position_poll_seconds
        self._opener = opener
        self.worker_name = f"{host.worker_prefix}:{worker_suffix}"
        self._lock = threading.Lock()
        self._link = ViscaLink(
            resend_interval_seconds(), host.clock.monotonic(), position_poll_seconds()
        )
        self._worker: WorkerContext | None = None
        self._logged_sequence_echo: bool | None = None
        self._published: dict[str, object] = {}

    # --- CameraDriver ---------------------------------------------------------------

    def connect(self) -> None:
        """Start the worker; nothing is sent until the first poll (never a stop)."""
        self._publish_status()
        self._host.start_worker(WorkerSpec(self.worker_name, self._run, starts_offline=True))

    def disconnect(self) -> None:
        """Stop the worker and close the socket."""
        self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Hold `velocity` for `ttl_ms`; the worker sends and re-sends it."""
        with self._lock:
            self._link.move(velocity, ttl_ms / 1000, self._host.clock.monotonic())
        self._wake()

    def stop(self) -> None:
        """Stop pan/tilt and zoom, sent a few times (see visca_link)."""
        with self._lock:
            self._link.stop(self._host.clock.monotonic())
        self._wake()

    def recall_preset(self, n: int) -> None:
        """Go to preset `n` (0-127)."""
        with self._lock:
            self._link.recall_preset(n)
        self._wake()

    def store_preset(self, n: int) -> None:
        """Store preset `n` (0-127)."""
        with self._lock:
            self._link.store_preset(n)
        self._wake()

    def position(self) -> Position | None:
        """The last position read back (polled every `position_poll_seconds`)."""
        with self._lock:
            return self._link.position()

    def capabilities(self) -> DriverCapabilities:
        """A PTZ with zoom and position feedback, presets 0-127, no auto mode."""
        return DriverCapabilities(
            kind=CameraKind.PTZ,
            has_position_feedback=True,
            has_zoom=True,
            supports_auto=False,
            preset_max=packets.PRESET_MAX,
        )

    def health(self) -> Status:
        """Judged from missed replies (see visca_link)."""
        with self._lock:
            return self._link.health

    def settled(self) -> bool:
        """At rest, with the last stop's sends done (see `ViscaLink.settled`)."""
        with self._lock:
            return self._link.settled

    # --- the worker -----------------------------------------------------------------

    def _wake(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.wake()

    def _run(self, worker: WorkerContext) -> None:
        clock = self._host.clock
        transport = self._open()
        self._worker = worker
        try:
            while not worker.should_stop:
                now = clock.monotonic()
                with self._lock:
                    self._link.resend_interval = self._resend_interval()
                    self._link.top_speed_steps = self._top_speed_steps()
                    self._link.position_poll_seconds = self._position_poll_seconds()
                    self._read_replies(transport, now)
                    self._link.expire_replies(now)
                    outgoing = self._link.due(now)
                    reopen = self._link.should_reopen(now)
                for packet in outgoing:
                    transport.send(packet.datagram)
                with self._lock:
                    # Answers already waiting (a fast camera, the in-process fake)
                    # are read now rather than after the next sleep.
                    self._read_replies(transport, clock.monotonic())
                    pause = self._link.seconds_until_next(clock.monotonic())
                if reopen:
                    self._host.logger.info(
                        "camera %s offline; reopening its socket", self._host.camera_id
                    )
                    transport.close()
                    transport = self._open()
                self._log_sequence_echo()
                self._report(worker)
                worker.sleep(pause)
        finally:
            self._worker = None
            transport.close()

    def _read_replies(self, transport: DatagramTransport, now: float) -> None:
        while (datagram := transport.receive()) is not None:
            self._link.received(datagram, now)

    def _open(self) -> DatagramTransport:
        try:
            return self._opener(self._address, self._port)
        except OSError as error:
            # e.g. a host name that does not resolve: offline, and the supervisor retries.
            raise DeviceOfflineError(
                f"cannot open {self._address}:{self._port}: {error}"
            ) from error

    def _log_sequence_echo(self) -> None:
        """Say once in the log which kind of camera this is (it explains its health rule)."""
        with self._lock:
            echo = self._link.replies.sequence_echo
        if echo is None or echo == self._logged_sequence_echo:
            return
        self._logged_sequence_echo = echo
        if echo:
            self._host.logger.info("camera %s echoes VISCA sequence numbers", self._host.camera_id)
        else:
            self._host.logger.info(
                "camera %s does not echo VISCA sequence numbers; judging it by any reply",
                self._host.camera_id,
            )

    def _report(self, worker: WorkerContext) -> None:
        with self._lock:
            status, reason = self._link.health, self._link.health_reason
        worker.report_health(_HEALTH_STATE[status], reason)
        self._publish_status()

    def _publish_status(self) -> None:
        """Write the CameraState fields that changed (status, position, moving)."""
        with self._lock:
            position = self._link.position()
            # Status last, so a page that sees "ok" also sees the position with it.
            fields: dict[str, object] = {
                # None whenever the position is not known now (see visca_replies).
                "position": position.model_dump() if position else None,
                "moving": self._link.moving,
                "last_error": self._link.last_error,
                "status_reason": self._link.health_reason,
                "status": self._link.health.value,
            }
        for key, value in fields.items():
            if self._published.get(key, ...) != value:
                self._published[key] = value
                self._host.publish(key, value)
