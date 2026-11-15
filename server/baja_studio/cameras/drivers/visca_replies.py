"""Reading a VISCA/IP camera's replies: what they answer, where the camera is, how it is doing.

Told the time on every call, like `visca_link`, which owns one `ReplyTracker`.

Two kinds of camera (ADR 0003, "Decisions after the bench"):

- **Cameras that echo our sequence numbers** (the VISCA/IP spec; the SMTAV
  as ptz_test.py drove it). Each reply is matched to the packet it answers.
  A packet unanswered after `REPLY_TIMEOUT_SECONDS` is a miss: three in a
  row make the camera `degraded`, ten `offline`.
- **Cameras that do not** (the bench PTZ at 192.168.10.245, probes of
  2026-11-15): every reply carries the camera's own counter, which rises
  ~40/s on its own. Replies come late (seconds at first) and batched. So a
  reply cannot tell which packet it answers, and the camera is judged by
  silence instead: `degraded` after `SILENCE_FOR_DEGRADED_SECONDS` with
  nothing at all heard while we were sending, `offline` after
  `SILENCE_FOR_OFFLINE_SECONDS`. The longest gap measured while sending was
  6 s, so neither flaps on the batching. Its ACKs and completions come
  reliably, so any inquiry keeps it judged.

Which kind a camera is, is decided from what it sends (`sequence_echo`):
a reply carrying one of our pending numbers says "echo"; three replies in a
row carrying numbers we never sent say "no echo". The latest evidence wins.

Position is published only while it can be trusted, otherwise None (never a
stale value presented as current):

- from an echoing camera, an answer is read by the inquiry it answers;
- from one that does not echo, an 11-byte `90 50` answer is pan/tilt and a
  7-byte one zoom only while a zoom inquiry is the only one outstanding (the
  bench PTZ sends 7-byte answers nobody asked for), and only once no motion
  was sent for `REST_BEFORE_POSITION_SECONDS` (it answered nothing while
  moving, and late answers may predate a move). Sending motion forgets its
  position;
- a position not refreshed for `POSITION_MAX_AGE_SECONDS` is dropped.
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum

from baja_studio.agents.contract import Position, Status
from baja_studio.cameras.drivers import visca_packets as packets

REPLY_TIMEOUT_SECONDS = 0.5
MISSED_FOR_DEGRADED = 3
MISSED_FOR_OFFLINE = 10
SILENCE_FOR_DEGRADED_SECONDS = 7.0
SILENCE_FOR_OFFLINE_SECONDS = 12.0
UNKNOWN_SEQUENCES_FOR_NO_ECHO = 3
POSITION_MAX_AGE_SECONDS = 30.0
# The bench PTZ's batches came up to ~3 s late: an answer sooner after motion may predate it.
REST_BEFORE_POSITION_SECONDS = 3.0
# How many of our latest sequence numbers a reply may still carry (late
# completions, the second answer of a pair) without counting as "not ours".
RECENT_SEQUENCES_KEPT = 64
# Times are floats: 0.3 - 0.2 is just under 0.1. Deadlines this close count as reached.
TIME_TOLERANCE_SECONDS = 1e-6


class Purpose(Enum):
    """Why a packet was sent; decides what its reply means."""

    MOTION = "motion"
    STOP = "stop"
    PRESET = "preset"
    PAN_TILT_INQUIRY = "pan_tilt_inquiry"
    ZOOM_INQUIRY = "zoom_inquiry"


INQUIRIES = (Purpose.PAN_TILT_INQUIRY, Purpose.ZOOM_INQUIRY)
# Packets after which the camera may be somewhere else (a preset store does
# not move it, but the purpose does not tell them apart: count it too).
MOVES_THE_CAMERA = (Purpose.MOTION, Purpose.STOP, Purpose.PRESET)


@dataclass
class _Pending:
    purpose: Purpose
    sent_at: float


class ReplyTracker:
    """Everything learnt from one camera's replies."""

    def __init__(self) -> None:
        """A tracker that has heard nothing yet (the camera is offline until it answers)."""
        # True: the camera echoes our numbers; False: it does not; None: not known yet.
        self.sequence_echo: bool | None = None
        self.last_error = ""
        self.status = Status.OFFLINE
        self.reason = "no answer from the camera yet"
        self._pending: dict[int, _Pending] = {}
        # Our latest sequence numbers and why each was sent (a data answer may
        # follow its ACK under the same number, after the ACK was matched).
        self._recent: deque[int] = deque(maxlen=RECENT_SEQUENCES_KEPT)
        self._recent_purposes: dict[int, Purpose] = {}
        self._unknown_in_a_row = 0
        self._missed_in_a_row = 0
        self._ever_heard = False
        # The first packet sent since the camera was last heard from (None: nothing waiting).
        self._waiting_since: float | None = None
        self._last_motion_sent_at = float("-inf")
        self._raw_pan_tilt: tuple[int, int] | None = None
        self._raw_zoom: int | None = None
        self._pan_tilt_heard_at = 0.0

    def sent(self, sequence: int, purpose: Purpose, now: float) -> None:
        """Note a packet that just went out."""
        self._pending[sequence] = _Pending(purpose, now)
        if len(self._recent) == self._recent.maxlen:
            self._recent_purposes.pop(self._recent[0], None)
        self._recent.append(sequence)
        self._recent_purposes[sequence] = purpose
        if self._waiting_since is None:
            self._waiting_since = now
        if purpose in MOVES_THE_CAMERA:
            self._last_motion_sent_at = now
            if self.sequence_echo is not True:
                self._forget_position()  # its answers cannot be told from older ones

    def received(self, replies: list[packets.Reply], now: float) -> set[Purpose]:
        """Read the messages of one datagram; return the purposes of the packets they answer.

        Only a camera that echoes our numbers can answer a known packet, so the
        set is always empty for the other kind.
        """
        answered: set[Purpose] = set()
        for reply in replies:
            # Anything at all from the camera shows it is alive.
            self._ever_heard = True
            self._waiting_since = None
            purpose = self._match(reply)
            if purpose is not None:
                answered.add(purpose)
            if reply.kind == packets.ReplyKind.ERROR:
                what = f"a {purpose.value}" if purpose is not None else "a"
                self.last_error = f"camera refused {what} command"
            if reply.kind == packets.ReplyKind.INQUIRY_ANSWER:
                self._read_position(reply, now)
        return answered

    def review(self, now: float) -> None:
        """Count overdue replies as missed, judge the camera's health, drop an old position."""
        for sequence, pending in list(self._pending.items()):
            if now - pending.sent_at >= REPLY_TIMEOUT_SECONDS - TIME_TOLERANCE_SECONDS:
                del self._pending[sequence]
                self._missed_in_a_row += 1
        self.status, self.reason = self._judge(now)
        age = now - self._pan_tilt_heard_at
        if age >= POSITION_MAX_AGE_SECONDS - TIME_TOLERANCE_SECONDS:
            self._forget_position()

    def next_deadline(self) -> float | None:
        """When the next pending reply becomes overdue (None if nothing is pending)."""
        if not self._pending:
            return None
        return min(pending.sent_at for pending in self._pending.values()) + REPLY_TIMEOUT_SECONDS

    def position(self) -> Position | None:
        """Where the camera is, in degrees and 0..1 zoom, or None when that is not known now."""
        if self._raw_pan_tilt is None:
            return None
        raw_pan, raw_tilt = self._raw_pan_tilt
        zoom = 0.0 if self._raw_zoom is None else self._raw_zoom / packets.ZOOM_POSITION_TELE
        return Position(
            pan=raw_pan * packets.DEGREES_PER_PAN_TILT_STEP,
            tilt=raw_tilt * packets.DEGREES_PER_PAN_TILT_STEP,
            zoom=min(1.0, max(0.0, zoom)),
        )

    # --- inside -----------------------------------------------------------------------

    def _match(self, reply: packets.Reply) -> Purpose | None:
        """The purpose of the packet this reply answers, if it can tell; learns `sequence_echo`."""
        if reply.payload_type != packets.PAYLOAD_REPLY:
            return None  # a control message (e.g. a sequence reset's answer): only liveness
        pending = self._pending.pop(reply.sequence, None)
        if pending is not None:
            self.sequence_echo = True
            self._unknown_in_a_row = 0
            self._missed_in_a_row = 0
            return pending.purpose
        if reply.sequence in self._recent_purposes:
            # One of ours, already answered or overdue: the completion after an
            # ACK, or a late reply. It proves the echo but answers nothing now.
            self._unknown_in_a_row = 0
            return None
        self._unknown_in_a_row += 1
        if self._unknown_in_a_row >= UNKNOWN_SEQUENCES_FOR_NO_ECHO:
            self.sequence_echo = False
        return None

    def _read_position(self, reply: packets.Reply, now: float) -> None:
        if self.sequence_echo is True:
            # Read as what it answers; an answer to none of our inquiries is ignored.
            purpose = self._recent_purposes.get(reply.sequence)
            reads_pan_tilt = purpose == Purpose.PAN_TILT_INQUIRY
            reads_zoom = purpose == Purpose.ZOOM_INQUIRY
        else:
            if now - self._last_motion_sent_at < REST_BEFORE_POSITION_SECONDS:
                return  # it may have moved since this answer was made
            outstanding = {
                pending.purpose
                for pending in self._pending.values()
                if pending.purpose in INQUIRIES
            }
            reads_pan_tilt = True  # only pan/tilt answers are 11 bytes long
            reads_zoom = outstanding == {Purpose.ZOOM_INQUIRY}
        pan_tilt = packets.parse_pan_tilt_position(reply.payload) if reads_pan_tilt else None
        if pan_tilt is not None:
            self._raw_pan_tilt = pan_tilt
            self._pan_tilt_heard_at = now
        zoom = packets.parse_zoom_position(reply.payload) if reads_zoom else None
        if zoom is not None:
            self._raw_zoom = zoom

    def _forget_position(self) -> None:
        self._raw_pan_tilt = None
        self._raw_zoom = None

    def _judge(self, now: float) -> tuple[Status, str]:
        if not self._ever_heard:
            return Status.OFFLINE, "no answer from the camera yet"
        if self.sequence_echo is True:
            if self._missed_in_a_row >= MISSED_FOR_OFFLINE:
                return Status.OFFLINE, f"{self._missed_in_a_row} replies missed in a row"
            if self._missed_in_a_row >= MISSED_FOR_DEGRADED:
                return Status.DEGRADED, f"{self._missed_in_a_row} replies missed in a row"
            return Status.OK, ""
        silence = 0.0 if self._waiting_since is None else now - self._waiting_since
        if silence >= SILENCE_FOR_OFFLINE_SECONDS - TIME_TOLERANCE_SECONDS:
            return Status.OFFLINE, f"nothing heard from the camera for {silence:.0f} s"
        if silence >= SILENCE_FOR_DEGRADED_SECONDS - TIME_TOLERANCE_SECONDS:
            return Status.DEGRADED, f"nothing heard from the camera for {silence:.0f} s"
        return Status.OK, ""
