"""A fake OBS: a minimal obs-websocket v5 server with simulated media playback.

It speaks the handshake (Hello, Identify with optional password, Identified),
answers the requests in `fake_obs_requests.py`, plays media sources against
ffprobe durations or a fixed duration, and sends the events playout listens
to (program scene changed, media started and ended).

Run it for the bench without OBS:

    python -m baja_studio.fakes.fake_obs --port 4455

Tests reach it in-process through `fake_obs_in_process.py`; no port opened.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_protocol import (
    DEFAULT_PORT,
    RPC_VERSION,
    CloseCode,
    EventSubscription,
    OpCode,
    RequestStatus,
    authentication_string,
    decode,
    encode,
)
from baja_studio.core.clock import Clock, SystemClock
from baja_studio.fakes.fake_obs_requests import (
    FakeInput,
    FakeObsRequests,
    FakeRequestRefusedError,
    FakeSceneItem,
)

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_DURATION_SECONDS = 10.0
TICK_SECONDS = 0.1
FIXED_SALT = "fake-obs-salt"
FIXED_CHALLENGE = "fake-obs-challenge"
MEDIA_EVENTS = {"started": "MediaInputPlaybackStarted", "ended": "MediaInputPlaybackEnded"}

Deliver = Callable[[str], object]
CloseConnection = Callable[[int, str], object]


@dataclass(eq=False)
class FakeObsSession:
    """One client connection as the fake sees it."""

    deliver: Deliver
    close_connection: CloseConnection
    identified: bool = False
    event_subscriptions: int = 0


@dataclass
class RequestRecord:
    """A request the fake answered, kept so tests can check what playout asked for."""

    request_type: str
    data: dict[str, Any] = field(default_factory=dict)


class FakeObs(FakeObsRequests):
    """The fake's scenes, inputs, sessions and media clock."""

    def __init__(
        self,
        clock: Clock,
        password: str = "",
        duration_for: Callable[[str], float | None] | None = None,
    ) -> None:
        """An empty OBS; add scenes and inputs, or use `with_playout_scenes`."""
        self.clock = clock
        self.password = password
        self._duration_for = duration_for or (lambda _file: DEFAULT_MEDIA_DURATION_SECONDS)
        self.lock = threading.RLock()
        self.scenes: dict[str, list[FakeSceneItem]] = {}
        self.inputs: dict[str, FakeInput] = {}
        self.program_scene = ""
        # OBS's two default transitions, with their English names.
        self.transitions = ["Cut", "Fade"]
        self.transition_overrides: dict[str, tuple[str | None, int | None]] = {}
        self.requests: list[RequestRecord] = []
        self.program_history: list[str] = []
        # Files that never finish opening (tests of the cue timeout).
        self.stuck_files: set[str] = set()
        self._sessions: list[FakeObsSession] = []
        self._next_item_id = 1

    @classmethod
    def with_playout_scenes(
        cls,
        clock: Clock,
        scene_a: str = "Playback A",
        scene_b: str = "Playback B",
        source_a: str = "baja_program_a",
        source_b: str = "baja_program_b",
        other_scenes: tuple[str, ...] = ("Main",),
        **options: Any,
    ) -> "FakeObs":
        """An OBS set up as docs/bench/wp5.md says, with `other_scenes[0]` on program."""
        fake = cls(clock, **options)
        for scene in other_scenes:
            fake.add_scene(scene)
        fake.add_media_input(source_a)
        fake.add_media_input(source_b)
        fake.add_scene(scene_a, [source_a])
        fake.add_scene(scene_b, [source_b])
        fake.program_scene = other_scenes[0] if other_scenes else scene_a
        return fake

    def add_scene(self, name: str, sources: list[str] | None = None) -> None:
        """Create a scene holding `sources` (which must exist as inputs)."""
        with self.lock:
            items = []
            for source in sources or []:
                items.append(FakeSceneItem(self._next_item_id, source))
                self._next_item_id += 1
            self.scenes[name] = items
            if not self.program_scene:
                self.program_scene = name

    def add_media_input(self, name: str) -> None:
        """Create a media source (`ffmpeg_source`) with no file."""
        with self.lock:
            self.inputs[name] = FakeInput("ffmpeg_source")

    def now(self) -> float:
        """Media time: the clock's monotonic seconds."""
        return self.clock.monotonic()

    def duration_of(self, file: str) -> float | None:
        """How long a file plays, from the injected `duration_for`."""
        return self._duration_for(file) if file else None

    def requests_of(self, request_type: str) -> list[RequestRecord]:
        """The answered requests of one type, oldest first."""
        with self.lock:
            return [record for record in self.requests if record.request_type == request_type]

    def open_session(self, deliver: Deliver, close_connection: CloseConnection) -> FakeObsSession:
        """A client connected: greet it with Hello (asking for a password if set)."""
        session = FakeObsSession(deliver, close_connection)
        hello: dict[str, Any] = {"obsWebSocketVersion": "5.6.0", "rpcVersion": RPC_VERSION}
        if self.password:
            hello["authentication"] = {"challenge": FIXED_CHALLENGE, "salt": FIXED_SALT}
        with self.lock:
            self._sessions.append(session)
            session.deliver(encode(OpCode.HELLO, hello))
        return session

    def close_session(self, session: FakeObsSession) -> None:
        """The client went away; forget it."""
        with self.lock:
            if session in self._sessions:
                self._sessions.remove(session)

    def disconnect_all(self, code: int = 1001, reason: str = "OBS is quitting") -> None:
        """Close every connection, as OBS does when it quits."""
        with self.lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close_connection(code, reason)

    @property
    def session_count(self) -> int:
        """How many clients are connected."""
        with self.lock:
            return len(self._sessions)

    def receive(self, session: FakeObsSession, text: str) -> None:
        """Handle one message from a client."""
        with self.lock:
            self.tick()
            op, data = decode(text)
            if op == OpCode.IDENTIFY:
                self._identify(session, data)
            elif op == OpCode.REQUEST and session.identified:
                session.deliver(encode(OpCode.REQUEST_RESPONSE, self._answer(data)))

    def tick(self) -> None:
        """Let media time pass: opening sources start, finished ones end (with their events)."""
        with self.lock:
            now = self.now()
            for name, source in self.inputs.items():
                happened = source.media.advance(now)
                if happened is not None:
                    self.emit(
                        MEDIA_EVENTS[happened],
                        {"inputName": name},
                        EventSubscription.MEDIA_INPUTS,
                    )

    def emit(self, event_type: str, event_data: dict[str, Any], category: int) -> None:
        """Send an event to every identified client subscribed to its category."""
        message = encode(
            OpCode.EVENT,
            {"eventType": event_type, "eventIntent": category, "eventData": event_data},
        )
        with self.lock:
            for session in self._sessions:
                if session.identified and session.event_subscriptions & category:
                    session.deliver(message)

    def change_program_scene(self, scene_name: str) -> None:
        """Put a scene on program and tell the clients (also used by tests: "the operator cut")."""
        with self.lock:
            self.program_scene = scene_name
            self.program_history.append(scene_name)
            self.emit(
                "CurrentProgramSceneChanged", {"sceneName": scene_name}, EventSubscription.SCENES
            )

    def media_of(self, input_name: str) -> Any:
        """The `SimulatedMedia` of an input, for tests to inspect."""
        with self.lock:
            return self.inputs[input_name].media

    def _identify(self, session: FakeObsSession, data: dict[str, Any]) -> None:
        if self.password:
            expected = authentication_string(self.password, FIXED_SALT, FIXED_CHALLENGE)
            if data.get("authentication") != expected:
                self.close_session(session)
                session.close_connection(CloseCode.AUTHENTICATION_FAILED, "Authentication failed.")
                return
        session.identified = True
        session.event_subscriptions = int(data.get("eventSubscriptions", EventSubscription.ALL))
        session.deliver(encode(OpCode.IDENTIFIED, {"negotiatedRpcVersion": RPC_VERSION}))

    def _answer(self, data: dict[str, Any]) -> dict[str, Any]:
        request_type = str(data.get("requestType", ""))
        request_data = data.get("requestData") or {}
        response: dict[str, Any] = {"requestType": request_type, "requestId": data.get("requestId")}
        handler = getattr(self, f"_request_{request_type}", None)
        if handler is None:
            status = {"result": False, "code": RequestStatus.UNKNOWN_REQUEST_TYPE}
            response["requestStatus"] = {**status, "comment": f"unknown request {request_type}"}
            return response
        try:
            response_data = handler(request_data)
        except FakeRequestRefusedError as failure:
            response["requestStatus"] = {
                "result": False,
                "code": failure.code,
                "comment": failure.comment,
            }
            return response
        self.requests.append(RequestRecord(request_type, json.loads(json.dumps(request_data))))
        response["requestStatus"] = {"result": True, "code": RequestStatus.SUCCESS}
        if response_data:
            response["responseData"] = response_data
        return response


async def serve_fake_obs(fake: FakeObs, host: str, port: int) -> None:
    """Serve `fake` on a real WebSocket until cancelled, ticking media time."""
    from websockets.asyncio.server import ServerConnection, serve

    async def handle(connection: ServerConnection) -> None:
        loop = asyncio.get_running_loop()
        outgoing: asyncio.Queue[str | tuple[int, str]] = asyncio.Queue()
        session = fake.open_session(
            lambda text: loop.call_soon_threadsafe(outgoing.put_nowait, text),
            lambda code, reason: loop.call_soon_threadsafe(outgoing.put_nowait, (code, reason)),
        )

        async def send_forever() -> None:
            while True:
                item = await outgoing.get()
                if isinstance(item, tuple):
                    await connection.close(item[0], item[1])
                    return
                await connection.send(item)

        sender = asyncio.create_task(send_forever())
        try:
            async for message in connection:
                fake.receive(session, message if isinstance(message, str) else message.decode())
        finally:
            fake.close_session(session)
            sender.cancel()

    async with serve(handle, host, port):
        logger.info("fake OBS listening on ws://%s:%d", host, port)
        while True:
            fake.tick()
            await fake.clock.sleep(TICK_SECONDS)


def main(argv: list[str] | None = None) -> None:
    """Command line entry point: serve a fake OBS set up for playout."""
    parser = argparse.ArgumentParser(description="Serve a fake OBS (obs-websocket v5).")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument("--password", default="", help="require this password")
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_MEDIA_DURATION_SECONDS, help="clip length (s)"
    )
    parser.add_argument("--ffprobe", default=None, help="probe real durations with this ffprobe")
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fixed_duration: float = options.duration

    def duration_for(file: str) -> float | None:
        if options.ffprobe:
            from baja_studio.playout.probe import MediaProber, ProbeError

            try:
                return MediaProber(options.ffprobe, "ffmpeg").probe(file).duration_s
            except ProbeError:
                logger.warning("could not probe %s; using %.1f s", file, fixed_duration)
        return fixed_duration

    fake = FakeObs.with_playout_scenes(
        SystemClock(), password=options.password, duration_for=duration_for
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve_fake_obs(fake, options.host, options.port))


if __name__ == "__main__":
    main()
