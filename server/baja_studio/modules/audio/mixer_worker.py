"""The `mixer` worker: keeps audio talking to the XR18 over OSC/UDP.

For each mixer address it opens a transport, creates an `Xr18Link` (the
timing rules) and loops: hand received datagrams to the controller, send
what the link says is due (keep-alive, `/xinfo`, queued writes and
queries), publish the connection and packet rates, sleep until the next
thing is due or something wakes it.

A reader thread per transport blocks on `receive` and queues datagrams
(it is not a supervisor worker and never touches the controller).

Health: `offline` while no address is set, while the address does not
resolve, and while `/xinfo` goes unanswered for 5 s; `ok` once it answers.
"""

import collections
import logging
import threading
from collections.abc import Callable

from baja_studio.adapters.xr18_link import OFFLINE_AFTER_S, Xr18Link
from baja_studio.adapters.xr18_transport import (
    TransportFactory,
    Xr18Transport,
    Xr18TransportClosedError,
    Xr18UnreachableError,
)
from baja_studio.audio.config import AudioSettings, MixerSettings
from baja_studio.core.worker import HealthState, WorkerContext
from baja_studio.modules.audio.controller import AudioController

IDLE_INTERVAL_SECONDS = 1.0
UNREACHABLE_RETRY_SECONDS = 4.0
# The longest the loop sleeps, so packet rates and settings changes are seen.
MAX_SLEEP_SECONDS = 1.0


class MixerWorker:
    """One mixer connection at a time."""

    def __init__(
        self,
        controller: AudioController,
        settings: Callable[[], AudioSettings],
        transport_factory: TransportFactory,
        logger: logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """The loop starts with `run`."""
        self._controller = controller
        self._settings = settings
        self._transport_factory = transport_factory
        self._logger = logger
        self._inbox: collections.deque[bytes] = collections.deque()
        self._inbox_lock = threading.Lock()
        self._worker: WorkerContext | None = None
        self._last_address: tuple[str, int] | None = None

    def wake(self) -> None:
        """Something to send, or the settings changed."""
        if self._worker is not None:
            self._worker.wake()

    def run(self, worker: WorkerContext) -> None:
        """Worker body: (re)connect to whatever address the settings name, until stopped."""
        self._worker = worker
        publisher = self._controller.publisher
        while not worker.should_stop:
            mixer = self._settings().mixer
            if not mixer.enabled or not mixer.host:
                self._controller.attach_link(None, forget_values=False)
                if not mixer.enabled:
                    publisher.mixer(connection="disabled", message="XR18 control is off")
                    worker.mark_online()
                    worker.report_health(HealthState.OK, "XR18 control is off")
                else:
                    message = "XR18 not configured: set its address in the audio setup"
                    publisher.mixer(connection="not_configured", host="", message=message)
                    worker.report_health(HealthState.OFFLINE, message)
                worker.sleep(IDLE_INTERVAL_SECONDS)
                continue
            try:
                transport = self._transport_factory(mixer.host, mixer.port)
            except Xr18UnreachableError as error:
                publisher.mixer(connection="offline", host=mixer.host, message=str(error))
                worker.report_health(HealthState.OFFLINE, str(error))
                worker.sleep(UNREACHABLE_RETRY_SECONDS)
                continue
            self._serve(worker, mixer, transport)

    def _serve(self, worker: WorkerContext, mixer: MixerSettings, transport: Xr18Transport) -> None:
        clock = worker.clock
        address = (mixer.host, mixer.port)
        link = Xr18Link(clock.monotonic())
        self._controller.attach_link(link, forget_values=address != self._last_address)
        self._last_address = address
        publisher = self._controller.publisher
        publisher.mixer(connection="connecting", host=mixer.host, message="")
        with self._inbox_lock:
            self._inbox.clear()
        reader = threading.Thread(
            target=self._read, args=(transport,), name="audio-xr18-reader", daemon=True
        )
        reader.start()
        started_at = clock.monotonic()
        online = False
        try:
            while not worker.should_stop and self._same_mixer(mixer):
                now = clock.monotonic()
                self._handle_inbox(link, now)
                if link.is_online(now) != online:
                    online = not online
                    self._announce(worker, link, online)
                elif not online and now - started_at >= OFFLINE_AFTER_S:
                    message = f"XR18 at {mixer.host} is not answering; is it on and reachable?"
                    publisher.mixer(connection="offline", message=message)
                    worker.report_health(HealthState.OFFLINE, message)
                for datagram in link.due_packets(now):
                    transport.send(datagram)
                rates = link.rates(now)
                publisher.mixer(packets_per_s={"sent": rates.sent, "received": rates.received})
                worker.sleep(min(link.next_wake(clock.monotonic()), MAX_SLEEP_SECONDS))
        finally:
            transport.close()
            reader.join(timeout=2.0)
            self._controller.attach_link(None, forget_values=False)

    def _announce(self, worker: WorkerContext, link: Xr18Link, online: bool) -> None:
        publisher = self._controller.publisher
        self._controller.set_online(online)
        if online:
            info = link.info
            publisher.mixer(
                connection="ok",
                message="",
                name=info.name if info else None,
                model=info.model if info else None,
                firmware=info.firmware if info else None,
            )
            worker.mark_online()
            worker.report_health(HealthState.OK)
            self._logger.info("XR18 answering (%s)", info.name if info else "?")
        else:
            message = "the XR18 stopped answering for 5 s; still trying"
            publisher.mixer(connection="offline", message=message)
            worker.report_health(HealthState.OFFLINE, message)
            self._logger.warning("%s", message)
        self.wake()

    def _handle_inbox(self, link: Xr18Link, now: float) -> None:
        with self._inbox_lock:
            datagrams = list(self._inbox)
            self._inbox.clear()
        if datagrams:
            self._controller.receive_from_mixer(link, datagrams, now)

    def _same_mixer(self, mixer: MixerSettings) -> bool:
        current = self._settings().mixer
        return current.enabled and (current.host, current.port) == (mixer.host, mixer.port)

    def _read(self, transport: Xr18Transport) -> None:
        """Reader thread: queue each datagram from the mixer and wake the worker."""
        while True:
            try:
                datagram = transport.receive()
            except Xr18TransportClosedError:
                return
            with self._inbox_lock:
                self._inbox.append(datagram)
            self.wake()
