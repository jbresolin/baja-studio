"""A fake VISCA-over-IP PTZ camera, for tests and for bench work without the SMTAV.

    python -m baja_studio.fakes.fake_visca_camera --port 52381 [--measured]

`FakeViscaCamera` is pure code told the time on every call: it parses each
datagram and integrates pan, tilt and zoom from the speed steps it was given.
It answers in one of two ways:

- **well-behaved** (the default, the VISCA/IP spec as ptz_test.py saw the
  SMTAV): ACK then completion (or the inquiry's answer) at once, each in its
  own datagram, with the command's sequence number;
- **measured** (`measured=MeasuredReplies()`, `--measured`): as the bench
  PTZ was measured answering on 2026-11-15 (`fake_visca_measured.py`): its
  own sequence numbers, late batched replies, inquiry data only sometimes.

It can be told to drop replies, to play an unplugged cable. Tests reach it
in-process through `InProcessDatagramTransport`; `main` serves it on a real
UDP port.
"""

import argparse
import contextlib
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.fakes import fake_visca_measured
from baja_studio.fakes.fake_visca_measured import MeasuredBehaviour, MeasuredReplies

# How fast the fake moves at the top speed step, in raw position steps per second.
PAN_TILT_STEPS_PER_SECOND_AT_TOP = 1200.0
ZOOM_STEPS_PER_SECOND_AT_TOP = 8000.0
PAN_TILT_LIMIT = 0x7FFF


@dataclass
class FakeAxis:
    """One axis: position in raw steps and its current rate (steps per second)."""

    position: float = 0.0
    rate: float = 0.0
    low: float = -PAN_TILT_LIMIT
    high: float = PAN_TILT_LIMIT

    def advance(self, seconds: float) -> None:
        """Move at the current rate, stopping at the end stops."""
        self.position = max(self.low, min(self.high, self.position + self.rate * seconds))


@dataclass
class FakeViscaCamera:
    """The fake camera's state and protocol handling."""

    pan: FakeAxis = field(default_factory=FakeAxis)
    tilt: FakeAxis = field(default_factory=FakeAxis)
    zoom: FakeAxis = field(default_factory=lambda: FakeAxis(low=0, high=packets.ZOOM_POSITION_TELE))
    presets: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    dropping_replies: bool = False
    # None: well-behaved; otherwise answer as the bench PTZ was measured doing.
    measured: MeasuredReplies | None = None
    # Every VISCA payload received, newest last (tests read it to check bytes).
    received: deque[bytes] = field(default_factory=lambda: deque(maxlen=1000))
    _last_time: float | None = None

    def handle(self, datagram: bytes, now: float) -> list[bytes]:
        """Process one datagram at time `now`; return the datagrams to answer with."""
        self.advance_to(now)
        reply = packets.parse_reply(datagram)  # the header layout is the same both ways
        if reply is None:
            return []
        payload, sequence = reply.payload, reply.sequence
        self.received.append(payload)
        answers = self._answer(payload, reply.payload_type)
        if self.dropping_replies:
            return []
        if self.measured is not None:
            return self.measured.release(answers, now)
        return [packets.wrap(answer, packets.PAYLOAD_REPLY, sequence) for answer in answers]

    def advance_to(self, now: float) -> None:
        """Integrate motion up to `now`."""
        if self._last_time is not None and now > self._last_time:
            for axis in (self.pan, self.tilt, self.zoom):
                axis.advance(now - self._last_time)
        self._last_time = now

    @property
    def moving(self) -> bool:
        """True while any axis has a rate."""
        return any(axis.rate != 0.0 for axis in (self.pan, self.tilt, self.zoom))

    def _answer(self, payload: bytes, payload_type: int) -> list[bytes]:
        ack = bytes([packets.REPLY_HEADER, 0x41, packets.TERMINATOR])
        done = bytes([packets.REPLY_HEADER, 0x51, packets.TERMINATOR])
        syntax_error = bytes([packets.REPLY_HEADER, 0x60, 0x02, packets.TERMINATOR])
        if payload_type == packets.PAYLOAD_INQUIRY:
            if payload == packets.pan_tilt_position_inquiry():
                data = self._pan_tilt_answer()
            elif payload == packets.zoom_position_inquiry():
                data = self._zoom_answer()
            else:
                return [syntax_error]
            if self.measured is not None:
                return self.measured.inquiry_answers(self._pan_tilt_answer(), self.moving)
            return [data]
        if self._apply_command(payload):
            if self.measured is not None:
                return [fake_visca_measured.ack(), fake_visca_measured.completion()]
            return [ack, done]
        return [syntax_error]

    def _apply_command(self, payload: bytes) -> bool:
        """Carry out a command payload; False if it is not one the fake knows."""
        if len(payload) == 9 and payload[1:4] == bytes([0x01, 0x06, 0x01]):
            pan_speed, tilt_speed, pan_direction, tilt_direction = payload[4:8]
            self.pan.rate = _rate(
                pan_speed, pan_direction, packets.PAN_SPEED_MAX, packets.PAN_RIGHT
            )
            self.tilt.rate = _rate(
                tilt_speed, tilt_direction, packets.TILT_SPEED_MAX, packets.TILT_UP
            )
            return True
        if len(payload) == 6 and payload[1:4] == bytes([0x01, 0x04, 0x07]):
            code = payload[4]
            speed = (code & 0x0F) / packets.ZOOM_SPEED_MAX * ZOOM_STEPS_PER_SECOND_AT_TOP
            self.zoom.rate = {0x20: speed, 0x30: -speed}.get(code & 0xF0, 0.0)
            return True
        if len(payload) == 7 and payload[1:4] == bytes([0x01, 0x04, 0x3F]):
            action, number = payload[4], payload[5]
            if action == 0x01:
                self.presets[number] = (self.pan.position, self.tilt.position, self.zoom.position)
                return True
            if action == 0x02:
                stored = self.presets.get(number)
                if stored is not None:
                    for axis, value in zip((self.pan, self.tilt, self.zoom), stored, strict=True):
                        axis.position, axis.rate = value, 0.0
                return True
        return False

    def _pan_tilt_answer(self) -> bytes:
        pan = round(self.pan.position) & 0xFFFF
        tilt = round(self.tilt.position) & 0xFFFF
        return (
            bytes([packets.REPLY_HEADER, 0x50])
            + packets.encode_nibbles(pan, 4)
            + packets.encode_nibbles(tilt, 4)
            + bytes([packets.TERMINATOR])
        )

    def _zoom_answer(self) -> bytes:
        zoom = round(self.zoom.position) & 0xFFFF
        return (
            bytes([packets.REPLY_HEADER, 0x50])
            + packets.encode_nibbles(zoom, 4)
            + bytes([packets.TERMINATOR])
        )


def _rate(speed: int, direction: int, top_speed: int, positive_direction: int) -> float:
    """Raw steps per second for a pan or tilt speed step and direction code."""
    # PAN_STOP and TILT_STOP are both 0x03.
    if direction == packets.PAN_STOP or speed == 0:
        return 0.0
    magnitude = speed / top_speed * PAN_TILT_STEPS_PER_SECOND_AT_TOP
    return magnitude if direction == positive_direction else -magnitude


class InProcessDatagramTransport:
    """A `DatagramTransport` that hands datagrams straight to a fake camera.

    `now` is the clock the fake is told the time with (the driver's clock in
    tests). Setting `unplugged` loses every datagram both ways.
    """

    def __init__(self, camera: FakeViscaCamera, now: Callable[[], float]) -> None:
        """Connect to `camera`."""
        self.camera = camera
        self._now = now
        self._inbox: deque[bytes] = deque()
        self._lock = threading.Lock()
        self.unplugged = False
        self.closed = False
        self.sent: list[bytes] = []
        # When each of `sent` was sent, on the `now` clock (tests count packets per second).
        self.sent_at: list[float] = []

    def send(self, datagram: bytes) -> None:
        """Deliver to the fake; its answers wait in our inbox."""
        with self._lock:
            now = self._now()
            self.sent.append(datagram)
            self.sent_at.append(now)
            if self.unplugged or self.closed:
                return
            self._inbox.extend(self.camera.handle(datagram, now))

    def receive(self) -> bytes | None:
        """The next answer, if any."""
        with self._lock:
            return self._inbox.popleft() if self._inbox and not self.unplugged else None

    def close(self) -> None:
        """Forget unread answers."""
        with self._lock:
            self.closed = True
            self._inbox.clear()


def serve(camera: FakeViscaCamera, port: int, host: str = "0.0.0.0") -> None:
    """Answer VISCA/IP on a real UDP port until interrupted."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((host, port))
        print(f"fake VISCA camera listening on udp://{host}:{port}", flush=True)
        while True:
            datagram, sender = server.recvfrom(1024)
            for answer in camera.handle(datagram, time.monotonic()):
                server.sendto(answer, sender)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    arguments = parse_arguments(argv)
    with contextlib.suppress(KeyboardInterrupt):
        serve(camera_for(arguments), arguments.port, arguments.host)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command line (see the module docstring)."""
    parser = argparse.ArgumentParser(description="A fake VISCA-over-IP PTZ camera")
    parser.add_argument("--port", type=int, default=52381)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--measured",
        action="store_true",
        help="answer as the bench PTZ was measured doing (own sequence numbers, late batched "
        "replies, inquiry data only sometimes)",
    )
    parser.add_argument(
        "--data-answer-probability",
        type=float,
        default=MeasuredBehaviour.data_answer_probability,
        help="with --measured: the share of inquiries answered with their data",
    )
    parser.add_argument("--seed", type=int, default=0, help="with --measured: the dice's seed")
    return parser.parse_args(argv)


def camera_for(arguments: argparse.Namespace) -> FakeViscaCamera:
    """The fake the command line asks for: well-behaved, or `--measured`."""
    camera = FakeViscaCamera()
    if arguments.measured:
        camera.measured = MeasuredReplies(
            MeasuredBehaviour(
                data_answer_probability=arguments.data_answer_probability, seed=arguments.seed
            )
        )
    return camera


if __name__ == "__main__":
    main()
