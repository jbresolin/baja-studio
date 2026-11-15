"""How the bench PTZ answers VISCA/IP, for the fake camera's measured mode.

    python -m baja_studio.fakes.fake_visca_camera --measured

Every behaviour here was measured on the real camera (192.168.10.245) with
two probe scripts on 2026-11-15 (`ptz_probe_script.py`: 267 packets sent,
355 datagrams back; a second round with other inquiry forms and a pan;
ADR 0003, "Decisions after the bench"):

- **Its own sequence numbers.** Every reply carries the camera's counter
  (~43000 at the time, rising about 40 a second even when nobody talks to
  it), never the number of the packet it answers.
- **Late, batched replies.** Nothing came back for the first ~3 s; after
  that, replies came 30-80 ms after one of our packets, often for earlier
  ones, and nothing ever came while we sent nothing. So answers wait in a
  queue that a later packet releases.
- **Several messages per datagram**, each with its own 8-byte header: up
  to four were seen in one datagram.
- **Inquiries answered by ACK + completion** (`90 48 FF`, `90 58 FF`)
  reliably, and only sometimes with data: 18 pan/tilt answers for 259
  inquiries in round 1, 5 for 20 in round 2. Data comes **in pairs**, an
  11-byte pan/tilt answer then a 7-byte `90 50 00 00 00 00 FF`, whichever
  inquiry was asked (pan/tilt or zoom), usually in the batch that answers a
  *later* request, and always the same values at rest. The fake's pair
  carries its real pan/tilt and that constant 7-byte answer.
- **No data while moving**: a 3 s pan at medium speed got none, only ACKs
  and completions.
- Not modelled: every reply also went to port 52381 of our host (the driver
  never listens there), and the answer to a sequence-reset control message
  (the driver never sends one).
"""

import random
from dataclasses import dataclass, field

from baja_studio.cameras.drivers import visca_packets as packets

# The socket number the bench PTZ put in its ACKs and completions (y in 90 4y FF).
MEASURED_SOCKET = 8
# The 7-byte answer that followed every pan/tilt answer, asked for or not.
CONSTANT_SHORT_ANSWER = bytes.fromhex("90 50 00 00 00 00 FF")


@dataclass
class MeasuredBehaviour:
    """The measured camera's numbers; tests may change them."""

    data_answer_probability: float = 0.1
    # Data pairs wait this much longer than the ACKs, so a later request releases them.
    data_delay: float = 0.5
    counter_start: int = 43000
    counter_per_second: float = 40.0
    first_reply_delay: float = 3.0
    reply_delay: float = 0.03
    max_messages_per_datagram: int = 4
    seed: int = 0


@dataclass
class _Queued:
    payload: bytes
    queued_at: float


@dataclass
class MeasuredReplies:
    """The measured camera's reply queue, told the time on every call."""

    behaviour: MeasuredBehaviour = field(default_factory=MeasuredBehaviour)
    _queue: list[_Queued] = field(default_factory=list)
    _data_pairs: list[bytes] = field(default_factory=list)
    _first_packet_at: float | None = None
    _last_sequence: int = -1
    _random: random.Random = field(init=False)

    def __post_init__(self) -> None:
        """Seed the dice that decide which inquiries get their data answered."""
        self._random = random.Random(self.behaviour.seed)

    def inquiry_answers(self, pan_tilt_answer: bytes, moving: bool) -> list[bytes]:
        """ACK and completion for a position inquiry (pan/tilt or zoom alike).

        Some of the time, and never while moving, the data pair (pan/tilt
        answer, constant 7-byte answer) is also set aside, to go out later
        than these (`release`).
        """
        if not moving and self._random.random() < self.behaviour.data_answer_probability:
            self._data_pairs += [pan_tilt_answer, CONSTANT_SHORT_ANSWER]
        return [ack(), completion()]

    def release(self, answers: list[bytes], now: float) -> list[bytes]:
        """Queue the answers to the packet received at `now`; return the datagrams due out.

        Only answers queued at least `reply_delay` ago go out, so a packet's
        own answers always wait for a later packet.
        """
        if self._first_packet_at is None:
            self._first_packet_at = now
        self._queue += [_Queued(answer, now) for answer in answers]
        late = now + self.behaviour.data_delay
        self._queue += [_Queued(answer, late) for answer in self._data_pairs]
        self._data_pairs = []
        if now - self._first_packet_at < self.behaviour.first_reply_delay:
            return []
        ready: list[_Queued] = []
        waiting: list[_Queued] = []
        for item in self._queue:
            (ready if now - item.queued_at >= self.behaviour.reply_delay else waiting).append(item)
        self._queue = waiting
        size = self.behaviour.max_messages_per_datagram
        return [
            b"".join(self._wrap(item.payload, now) for item in ready[start : start + size])
            for start in range(0, len(ready), size)
        ]

    @property
    def queued(self) -> int:
        """How many answers wait for a later packet."""
        return len(self._queue)

    def _wrap(self, payload: bytes, now: float) -> bytes:
        """One message with the camera's own counter: time-driven, never repeating."""
        assert self._first_packet_at is not None
        elapsed = now - self._first_packet_at
        counter = self.behaviour.counter_start + int(elapsed * self.behaviour.counter_per_second)
        self._last_sequence = max(counter, self._last_sequence + 1)
        return packets.wrap(payload, packets.PAYLOAD_REPLY, self._last_sequence)


def ack() -> bytes:
    """`90 48 FF`, the bench PTZ's ACK."""
    return bytes([packets.REPLY_HEADER, 0x40 | MEASURED_SOCKET, packets.TERMINATOR])


def completion() -> bytes:
    """`90 58 FF`, the bench PTZ's completion."""
    return bytes([packets.REPLY_HEADER, 0x50 | MEASURED_SOCKET, packets.TERMINATOR])
