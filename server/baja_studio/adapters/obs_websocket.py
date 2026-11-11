"""A small obs-websocket v5 client: handshake, requests with timeouts, events.

One `ObsWebsocketClient` holds at most one connection. A private reader
thread receives every message: it finishes the handshake, hands responses to
the request waiting for them (matched by `requestId`), and passes events to
the listeners. Requests may come from any thread except that reader. Nothing
here knows about playout; modules decide what to request and when.

Offline rule: connecting gives up after `connect_timeout_s` (3 s), each
request after `request_timeout_s` (2 s). A closed socket fails every waiting
request at once and tells the disconnect listeners.

Requests are numbered (1, 2, 3, ... per client). OBS sends everything on one
socket in order, so an event read after the response to request N was sent
after OBS handled N. `expecting_event(..., after_next_request=True)` uses
that to ignore events left over from before the request that should cause
them (a "started" of an earlier open of the same source).
"""

import contextlib
import itertools
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_protocol import (
    EventSubscription,
    OpCode,
    authentication_string,
    decode,
    identify_message,
    request_message,
)
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsAuthenticationError,
    ObsConnectionClosedError,
    ObsError,
    ObsNotConnectedError,
    ObsRequestFailedError,
    ObsRequestTimeoutError,
    ObsTransport,
    ObsUnreachableError,
    TransportFactory,
    open_websocket_transport,
)
from baja_studio.core.clock import Clock

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 2.0
AUTHENTICATION_FAILED_CLOSE_CODE = 4009

EventListener = Callable[[str, dict[str, Any]], None]
DisconnectListener = Callable[[str], None]


@dataclass
class _PendingRequest:
    answered: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


@dataclass(eq=False)
class ExpectedEvent:
    """An event a caller waits for (see `ObsWebsocketClient.expecting_event`)."""

    event_type: str
    match: dict[str, Any]
    # Set when a matching event arrives (once armed).
    arrived: threading.Event = field(default_factory=threading.Event)
    # Only events read after this request's response count (None: any event).
    request_number: int | None = None
    # True once that response was read (always, without a request number).
    armed: bool = True
    # The thread whose next request arms it, until that request is sent.
    arming_thread: int | None = None


@dataclass
class _Session:
    """One connection: its socket, handshake progress and waiting requests."""

    transport: ObsTransport
    password: str
    handshake_done: threading.Event = field(default_factory=threading.Event)
    identified: bool = False
    failure: ObsError | None = None
    closing: bool = False
    pending: dict[str, _PendingRequest] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader: threading.Thread | None = None


class ReconnectBackoff:
    """Delays between reconnect attempts: 0.5, 1, 2, then every 4 s."""

    def __init__(self, delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)) -> None:
        """Start at the first delay."""
        self._delays = delays
        self._attempt = 0

    def next_delay(self) -> float:
        """The wait before the next attempt; later calls wait longer, up to the last delay."""
        delay = self._delays[min(self._attempt, len(self._delays) - 1)]
        self._attempt += 1
        return delay

    def reset(self) -> None:
        """Back to the shortest delay after a successful connection."""
        self._attempt = 0


class ObsWebsocketClient(ObsRequestsMixin):
    """Connects to OBS, sends requests, and dispatches events to listeners."""

    def __init__(
        self,
        clock: Clock,
        transport_factory: TransportFactory = open_websocket_transport,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        event_subscriptions: int = EventSubscription.ALL,
    ) -> None:
        """Create a disconnected client; nothing happens until `connect`."""
        self._clock = clock
        self._transport_factory = transport_factory
        self._connect_timeout_s = connect_timeout_s
        self._request_timeout_s = request_timeout_s
        self._event_subscriptions = event_subscriptions
        self._session: _Session | None = None
        self._session_lock = threading.Lock()
        self._request_ids = itertools.count(1)
        self._event_listeners: list[EventListener] = []
        self._disconnect_listeners: list[DisconnectListener] = []
        self._expected_events: list[ExpectedEvent] = []
        self._expected_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """True while an identified connection is open."""
        session = self._session
        return session is not None and session.identified and session.failure is None

    def add_event_listener(self, listener: EventListener) -> None:
        """Call `listener(event_type, event_data)` for every event, on the reader thread.

        Listeners must be quick and must not make requests (the reader would
        be waiting for itself); hand the event to a worker instead.
        """
        self._event_listeners.append(listener)

    def add_disconnect_listener(self, listener: DisconnectListener) -> None:
        """Call `listener(reason)` when an identified connection drops (not on `close`)."""
        self._disconnect_listeners.append(listener)

    @contextlib.contextmanager
    def expecting_event(
        self, event_type: str, *, after_next_request: bool = False, **match: Any
    ) -> Iterator[ExpectedEvent]:
        """While inside, `arrived` is set when OBS sends `event_type` matching `match`.

        Used to wait for something OBS reports only as an event (a media
        source that started, for one): enter before the request that causes
        it, then wait on `arrived` with the clock. With `after_next_request`,
        the next request this thread sends arms it (`request_number`), and
        only events read after that request's response count.
        """
        expected = ExpectedEvent(event_type, match)
        if after_next_request:
            expected.armed = False
            expected.arming_thread = threading.get_ident()
        with self._expected_lock:
            self._expected_events.append(expected)
        try:
            yield expected
        finally:
            with self._expected_lock:
                self._expected_events.remove(expected)

    def connect(self, address: ObsAddress, password: str) -> None:
        """Open a connection and identify; returns once OBS accepted us.

        Raises `ObsUnreachableError` (nothing answered, or no handshake in
        time) or `ObsAuthenticationError` (password refused or missing).
        An existing connection is closed first.
        """
        self.close()
        transport = self._transport_factory(address, self._connect_timeout_s)
        session = _Session(transport, password)
        session.reader = threading.Thread(
            target=self._read_forever, args=(session,), name="obs-websocket-reader", daemon=True
        )
        with self._session_lock:
            self._session = session
        session.reader.start()
        finished = self._clock.wait(session.handshake_done, self._connect_timeout_s)
        if not finished or session.failure is not None:
            failure = session.failure or ObsUnreachableError(
                f"OBS at {address.uri} did not finish the handshake in time"
            )
            self.close()
            raise failure

    def close(self) -> None:
        """Close the connection, if any, and wait for the reader thread to end."""
        with self._session_lock:
            session, self._session = self._session, None
        if session is None:
            return
        session.closing = True
        session.transport.close()
        if session.reader is not None and session.reader is not threading.current_thread():
            session.reader.join(timeout=5.0)
        self._fail_pending(session)

    def request(
        self,
        request_type: str,
        request_data: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Send a request and return its `responseData` (empty dict if none).

        Raises `ObsNotConnectedError`, `ObsRequestTimeoutError` or
        `ObsRequestFailedError`.
        """
        session = self._session
        if session is None or not session.identified or session.failure is not None:
            raise ObsNotConnectedError("not connected to OBS")
        if threading.current_thread() is session.reader:
            raise RuntimeError("OBS requests cannot be made from an event listener")
        request_number = next(self._request_ids)
        request_id = str(request_number)
        pending = _PendingRequest()
        with session.lock:
            session.pending[request_id] = pending
        self._arm_expected_events(request_number)
        try:
            session.transport.send(request_message(request_type, request_id, request_data or {}))
        except ObsConnectionClosedError as error:
            raise ObsNotConnectedError(f"lost OBS while sending {request_type}") from error
        answered = self._clock.wait(pending.answered, timeout_s or self._request_timeout_s)
        with session.lock:
            session.pending.pop(request_id, None)
        if not answered:
            raise ObsRequestTimeoutError(f"OBS did not answer {request_type} in time")
        if pending.response is None:
            raise ObsNotConnectedError(f"lost OBS while waiting for {request_type}")
        status = pending.response.get("requestStatus", {})
        if not status.get("result"):
            raise ObsRequestFailedError(
                request_type, int(status.get("code", 0)), str(status.get("comment", ""))
            )
        data = pending.response.get("responseData")
        return data if isinstance(data, dict) else {}

    def _read_forever(self, session: _Session) -> None:
        """Reader thread body: handle messages until the socket closes."""
        try:
            while True:
                self._handle_message(session, session.transport.receive())
        except ObsConnectionClosedError as closed:
            self._connection_lost(session, closed)
        except Exception as error:
            # A malformed message means we no longer understand this OBS; drop the link.
            logger.exception("unexpected message from OBS; closing the connection")
            session.transport.close()
            self._connection_lost(session, ObsConnectionClosedError(None, str(error)))

    def _handle_message(self, session: _Session, text: str) -> None:
        op, data = decode(text)
        if op == OpCode.HELLO:
            self._answer_hello(session, data)
        elif op == OpCode.IDENTIFIED:
            session.identified = True
            session.handshake_done.set()
        elif op == OpCode.REQUEST_RESPONSE:
            self._response_read(str(data.get("requestId")))
            with session.lock:
                pending = session.pending.get(str(data.get("requestId")))
            if pending is not None:
                pending.response = data
                pending.answered.set()
        elif op == OpCode.EVENT:
            self._dispatch_event(str(data.get("eventType", "")), data.get("eventData") or {})

    def _answer_hello(self, session: _Session, hello: dict[str, Any]) -> None:
        challenge = hello.get("authentication")
        authentication = None
        if isinstance(challenge, dict):
            if not session.password:
                session.failure = ObsAuthenticationError(
                    "OBS asks for a password; set it in the playout settings"
                )
                session.handshake_done.set()
                session.transport.close()
                return
            authentication = authentication_string(
                session.password, str(challenge.get("salt", "")), str(challenge.get("challenge"))
            )
        session.transport.send(identify_message(authentication, self._event_subscriptions))

    def _arm_expected_events(self, request_number: int) -> None:
        """This thread is sending request `request_number`: it arms what it waits for."""
        thread = threading.get_ident()
        with self._expected_lock:
            for expected in self._expected_events:
                if expected.arming_thread == thread:
                    expected.arming_thread = None
                    expected.request_number = request_number

    def _response_read(self, request_id: str) -> None:
        """Reader thread: events read from now on came after that request was handled."""
        with self._expected_lock:
            for expected in self._expected_events:
                if not expected.armed and str(expected.request_number) == request_id:
                    expected.armed = True

    def _dispatch_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        with self._expected_lock:
            for expected in self._expected_events:
                if (
                    expected.armed
                    and expected.event_type == event_type
                    and all(event_data.get(key) == value for key, value in expected.match.items())
                ):
                    expected.arrived.set()
        for listener in list(self._event_listeners):
            try:
                listener(event_type, event_data)
            except Exception:
                logger.exception("OBS event listener failed on %s", event_type)

    def _connection_lost(self, session: _Session, closed: ObsConnectionClosedError) -> None:
        was_identified = session.identified
        if not was_identified and session.failure is None:
            if closed.code == AUTHENTICATION_FAILED_CLOSE_CODE:
                session.failure = ObsAuthenticationError("OBS refused the password")
            else:
                session.failure = ObsUnreachableError(f"OBS closed the connection: {closed}")
        elif session.failure is None:
            session.failure = ObsNotConnectedError(str(closed))
        session.handshake_done.set()
        self._fail_pending(session)
        if was_identified and not session.closing:
            for listener in list(self._disconnect_listeners):
                try:
                    listener(str(closed))
                except Exception:
                    logger.exception("OBS disconnect listener failed")

    def _fail_pending(self, session: _Session) -> None:
        with session.lock:
            waiting = list(session.pending.values())
        for pending in waiting:
            pending.answered.set()
