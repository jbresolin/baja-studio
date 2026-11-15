"""The VISCA/IP driver's logic, without sockets or threads: told the time on every call.

`ViscaLink` decides what to put on the wire and when; its `ReplyTracker`
(`visca_replies.py`) reads the camera's answers and judges its health.
`drivers/visca_ip.py` runs it in a worker with a UDP socket. Rules, from the
WP2 brief, ptz_test.py and the bench of 2026-11-15 (ADR 0003, "Decisions
after the bench"):

- A held velocity is re-sent every `resend_interval` (UDP loses packets) and
  stopped when `move` has not been called for its `ttl`: the camera itself
  has no dead-man switch.
- A stop is pan-tilt stop *and* zoom stop. It is sent `STOP_SENDS` times,
  `resend_interval` apart, or fewer if a camera that echoes our sequence
  numbers answers it first. Never more: endless stop resends (a camera that
  never "confirms") choked the bench PTZ until its video froze.
- If nothing at all was heard from the camera while a stop was sent and for
  `REPLY_TIMEOUT_SECONDS` after (cable out), the stop is owed: it is sent
  again, `STOP_SENDS` times and only once more, when the camera is next heard
  from, so it does not run away when the cable is back.
- Position is polled with one inquiry in flight at a time, pan/tilt and zoom
  in turn, one every `position_poll_seconds` (0: no position polling and no
  position published, only a pan/tilt inquiry every `LIVENESS_POLL_SECONDS`
  whose ACK and completion show the camera is there). The bench PTZ gives no
  usable position, so it runs with 0.
"""

from dataclasses import dataclass

from baja_studio.agents.contract import Position, Status
from baja_studio.cameras.driver import AT_REST, Velocity
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_replies import (
    REPLY_TIMEOUT_SECONDS,
    TIME_TOLERANCE_SECONDS,
    Purpose,
    ReplyTracker,
)

DEFAULT_POSITION_POLL_SECONDS = 1.0
LIVENESS_POLL_SECONDS = 3.0
STOP_SENDS = 3
REOPEN_WHILE_OFFLINE_SECONDS = 5.0
# A worker with nothing due still wakes this often to read replies promptly.
MAX_IDLE_SECONDS = 0.1

__all__ = ["Outgoing", "Purpose", "ViscaLink"]


@dataclass(frozen=True)
class Outgoing:
    """A datagram to send, already wrapped in its VISCA/IP header."""

    datagram: bytes
    purpose: Purpose


class ViscaLink:
    """What to send to one VISCA/IP camera, and how it is doing."""

    def __init__(
        self,
        resend_interval: float,
        now: float,
        position_poll_seconds: float = DEFAULT_POSITION_POLL_SECONDS,
    ) -> None:
        """`resend_interval` and `position_poll_seconds` in seconds (the driver
        updates both live); `now` is the clock's monotonic time."""
        self.resend_interval = resend_interval
        self.position_poll_seconds = position_poll_seconds
        # The camera's top pan and tilt speed steps (settings, read live by the driver).
        self.top_speed_steps = (
            packets.DEFAULT_PAN_SPEED_MAX_STEP,
            packets.DEFAULT_TILT_SPEED_MAX_STEP,
        )
        self.replies = ReplyTracker()
        self._sequence = packets.SequenceCounter()
        self._velocity = AT_REST
        self._velocity_expires_at = 0.0
        self._last_motion_sent_at = float("-inf")
        self._sent_velocity: Velocity | None = None
        # The current stop: sends still to make, when the last went out (None:
        # no stop in progress or owed), and whether the camera said anything meanwhile.
        self._stop_sends_left = 0
        self._stop_last_sent_at: float | None = None
        self._stop_heard = False
        self._queued: list[tuple[bytes, Purpose]] = []
        self._next_poll_at = now
        self._poll_zoom_next = False
        self._inquiry_sent_at: float | None = None
        self._last_reopen_at = now

    # --- what the driver asks for -------------------------------------------------

    def move(self, velocity: Velocity, ttl: float, now: float) -> None:
        """Hold `velocity` for `ttl` seconds from now; a zero velocity is a stop."""
        if velocity.is_zero:
            self.stop(now)
            return
        self._velocity = velocity
        self._velocity_expires_at = now + ttl
        self._stop_sends_left = 0
        self._stop_last_sent_at = None

    def stop(self, now: float) -> None:
        """Stop pan, tilt and zoom now; sent up to `STOP_SENDS` times."""
        self._velocity = AT_REST
        self._sent_velocity = None
        self._stop_sends_left = STOP_SENDS
        self._stop_last_sent_at = None
        self._stop_heard = False
        self._last_motion_sent_at = float("-inf")  # send the stop on the next tick

    def recall_preset(self, n: int) -> None:
        """Queue a preset recall; it also ends any held velocity (the camera moves to it)."""
        self._velocity = AT_REST
        self._sent_velocity = None
        self._queued.append((packets.preset_recall(n), Purpose.PRESET))

    def store_preset(self, n: int) -> None:
        """Queue a preset store."""
        self._queued.append((packets.preset_store(n), Purpose.PRESET))

    # --- the worker's loop ----------------------------------------------------------

    def due(self, now: float) -> list[Outgoing]:
        """Everything to send at `now`: expiries, stops, resends, queued commands, a poll."""
        if not self._velocity.is_zero and now >= self._velocity_expires_at - TIME_TOLERANCE_SECONDS:
            self.stop(now)  # the dead-man: nobody renewed the velocity
        outgoing: list[Outgoing] = []
        resend_due = (
            now - self._last_motion_sent_at >= self.resend_interval - TIME_TOLERANCE_SECONDS
        )
        if self._stop_sends_left > 0 and resend_due:
            outgoing += self._wrap_all(
                [(packets.pan_tilt_stop(), Purpose.STOP), (packets.zoom_stop(), Purpose.STOP)], now
            )
            self._last_motion_sent_at = now
            self._stop_last_sent_at = now
            self._stop_sends_left -= 1
        elif not self._velocity.is_zero and (resend_due or self._velocity != self._sent_velocity):
            outgoing += self._wrap_all(
                [
                    (packets.pan_tilt_for(self._velocity, *self.top_speed_steps), Purpose.MOTION),
                    (packets.zoom_for(self._velocity), Purpose.MOTION),
                ],
                now,
            )
            self._sent_velocity = self._velocity
            self._last_motion_sent_at = now
        outgoing += self._wrap_all(self._queued, now)
        self._queued = []
        outgoing += self._poll_if_due(now)
        return outgoing

    def received(self, datagram: bytes, now: float) -> None:
        """A datagram arrived from the camera: read every message in it."""
        replies = packets.parse_replies(datagram)
        if not replies:
            return  # not VISCA/IP at all: says nothing about the camera
        answered = self.replies.received(replies, now)
        self._inquiry_sent_at = None  # it is talking: the next inquiry may go
        self._follow_stop(answered, now)
        self.replies.review(now)

    def expire_replies(self, now: float) -> None:
        """Count every packet whose reply is overdue as missed, and judge the camera's health."""
        self.replies.review(now)

    def should_reopen(self, now: float) -> bool:
        """True when the socket should be closed and opened again (offline for a while)."""
        if self.health != Status.OFFLINE:
            return False
        if now - self._last_reopen_at < REOPEN_WHILE_OFFLINE_SECONDS:
            return False
        self._last_reopen_at = now
        return True

    def seconds_until_next(self, now: float) -> float:
        """How long the worker may sleep before something is due."""
        deadlines = [self._next_poll_at, now + MAX_IDLE_SECONDS]
        if self._inquiry_sent_at is not None:
            deadlines.append(self._inquiry_sent_at + REPLY_TIMEOUT_SECONDS)
        if not self._velocity.is_zero:
            deadlines += [
                self._velocity_expires_at,
                self._last_motion_sent_at + self.resend_interval,
            ]
        if self._stop_sends_left > 0:
            deadlines.append(self._last_motion_sent_at + self.resend_interval)
        reply_deadline = self.replies.next_deadline()
        if reply_deadline is not None:
            deadlines.append(reply_deadline)
        return max(0.0, min(deadlines) - now)

    # --- what it observed -------------------------------------------------------------

    @property
    def health(self) -> Status:
        """ok, degraded or offline, as last judged (see visca_replies)."""
        return self.replies.status

    @property
    def health_reason(self) -> str:
        """A sentence for the operator, empty when ok."""
        return self.replies.reason

    @property
    def last_error(self) -> str:
        """The last refusal the camera sent, worded for the operator."""
        return self.replies.last_error

    @property
    def moving(self) -> bool:
        """True while a velocity is held."""
        return not self._velocity.is_zero

    @property
    def settled(self) -> bool:
        """At rest, every send of the last stop made, and no stop owed to a silent camera."""
        stop_owed = self._stop_last_sent_at is not None and not self._stop_heard
        return self._velocity.is_zero and self._stop_sends_left == 0 and not stop_owed

    @property
    def velocity(self) -> Velocity:
        """The velocity being held (zero at rest)."""
        return self._velocity

    def position(self) -> Position | None:
        """Where the camera is (degrees, 0..1 zoom); None when not known now or not polled."""
        if self.position_poll_seconds <= 0:
            return None  # only liveness inquiries: their rare data answers are not polled for
        return self.replies.position()

    # --- inside -----------------------------------------------------------------------

    def _follow_stop(self, answered: set[Purpose], now: float) -> None:
        """What a message from the camera means for the stop being sent, if any."""
        if self._stop_last_sent_at is None:
            return
        since_last_send = now - self._stop_last_sent_at
        if self._stop_sends_left > 0 or since_last_send <= REPLY_TIMEOUT_SECONDS:
            if Purpose.STOP in answered:
                # An echoing camera confirmed it: no need to repeat.
                self._stop_heard = True
                self._stop_sends_left = 0
            elif self.replies.sequence_echo is not True:
                # A camera whose replies cannot be matched: hearing anything
                # while the stop goes out is the best sign it got it.
                self._stop_heard = True
            return
        # The stop's sends are over and the camera talks again.
        owed = not self._stop_heard
        self._stop_last_sent_at = None
        if owed and self._velocity.is_zero:
            # Nothing was heard while it went out (cable out?): send it once more.
            self.stop(now)
            self._stop_heard = True  # and never owe this one again

    def _poll_if_due(self, now: float) -> list[Outgoing]:
        """One inquiry, if none is in flight and the poll interval has passed."""
        if self._inquiry_sent_at is not None:
            if now - self._inquiry_sent_at < REPLY_TIMEOUT_SECONDS - TIME_TOLERANCE_SECONDS:
                return []  # still waiting for something from the camera
            self._inquiry_sent_at = None
        if now < self._next_poll_at - TIME_TOLERANCE_SECONDS:
            return []
        polling_position = self.position_poll_seconds > 0
        interval = self.position_poll_seconds if polling_position else LIVENESS_POLL_SECONDS
        self._next_poll_at = now + interval
        self._inquiry_sent_at = now
        if polling_position and self._poll_zoom_next:
            inquiry = (packets.zoom_position_inquiry(), Purpose.ZOOM_INQUIRY)
        else:
            inquiry = (packets.pan_tilt_position_inquiry(), Purpose.PAN_TILT_INQUIRY)
        self._poll_zoom_next = polling_position and not self._poll_zoom_next
        return self._wrap_all([inquiry], now, payload_type=packets.PAYLOAD_INQUIRY)

    def _wrap_all(
        self,
        items: list[tuple[bytes, Purpose]],
        now: float,
        payload_type: int = packets.PAYLOAD_COMMAND,
    ) -> list[Outgoing]:
        outgoing = []
        for payload, purpose in items:
            sequence = self._sequence.take()
            self.replies.sent(sequence, purpose, now)
            outgoing.append(Outgoing(packets.wrap(payload, payload_type, sequence), purpose))
        return outgoing
