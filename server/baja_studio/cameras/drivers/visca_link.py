"""The VISCA/IP driver's logic, without sockets or threads: told the time on every call.

`ViscaLink` decides what to put on the wire and when, and judges the camera's
health from its replies. `drivers/visca_ip.py` runs it in a worker with a UDP
socket. Rules, from the WP2 brief and ptz_test.py:

- A held velocity is re-sent every `resend_interval` (UDP loses packets) and
  stopped when `move` has not been called for its `ttl`: the camera itself
  has no dead-man switch.
- A stop is pan-tilt stop *and* zoom stop, and is repeated until the camera
  answers one of them, so a stop sent while the cable is out still lands
  when it is back (no runaway).
- Every packet expects a reply within 0.5 s. Three missed in a row:
  `degraded`; ten: `offline`, and the socket is reopened every few seconds.
- Position is asked for twice a second, which also tells an idle camera's health.
"""

from dataclasses import dataclass
from enum import Enum

from baja_studio.agents.contract import Position, Status
from baja_studio.cameras.driver import AT_REST, Velocity
from baja_studio.cameras.drivers import visca_packets as packets

REPLY_TIMEOUT_SECONDS = 0.5
MISSED_FOR_DEGRADED = 3
MISSED_FOR_OFFLINE = 10
POSITION_POLL_SECONDS = 0.5
REOPEN_WHILE_OFFLINE_SECONDS = 5.0
# A worker with nothing due still wakes this often to read replies promptly.
MAX_IDLE_SECONDS = 0.1
# Times are floats: 0.3 - 0.2 is just under 0.1. Deadlines this close count as reached.
TIME_TOLERANCE_SECONDS = 1e-6


class Purpose(Enum):
    """Why a packet was sent; decides what its reply means."""

    MOTION = "motion"
    STOP = "stop"
    PRESET = "preset"
    PAN_TILT_INQUIRY = "pan_tilt_inquiry"
    ZOOM_INQUIRY = "zoom_inquiry"


@dataclass(frozen=True)
class Outgoing:
    """A datagram to send, already wrapped in its VISCA/IP header."""

    datagram: bytes
    purpose: Purpose


@dataclass
class _Pending:
    purpose: Purpose
    sent_at: float


class ViscaLink:
    """What to send to one VISCA/IP camera, and how it is doing."""

    def __init__(self, resend_interval: float, now: float) -> None:
        """`resend_interval` in seconds; `now` is the clock's monotonic time."""
        self.resend_interval = resend_interval
        # The camera's top pan and tilt speed steps (settings, read live by the driver).
        self.top_speed_steps = (
            packets.DEFAULT_PAN_SPEED_MAX_STEP,
            packets.DEFAULT_TILT_SPEED_MAX_STEP,
        )
        self._sequence = packets.SequenceCounter()
        self._velocity = AT_REST
        self._velocity_expires_at = 0.0
        self._last_motion_sent_at = float("-inf")
        self._sent_velocity: Velocity | None = None
        self._stop_unconfirmed = False
        self._queued: list[tuple[bytes, Purpose]] = []
        self._pending: dict[int, _Pending] = {}
        self._next_poll_at = now
        self._missed_in_a_row = 0
        self._ever_answered = False
        self._went_offline_at: float | None = None
        self._last_reopen_at = now
        self._raw_pan_tilt: tuple[int, int] | None = None
        self._raw_zoom: int | None = None
        self.last_error = ""

    # --- what the driver asks for -------------------------------------------------

    def move(self, velocity: Velocity, ttl: float, now: float) -> None:
        """Hold `velocity` for `ttl` seconds from now; a zero velocity is a stop."""
        if velocity.is_zero:
            self.stop(now)
            return
        self._velocity = velocity
        self._velocity_expires_at = now + ttl
        self._stop_unconfirmed = False

    def stop(self, now: float) -> None:
        """Stop pan, tilt and zoom now, and keep saying so until the camera answers."""
        self._velocity = AT_REST
        self._sent_velocity = None
        self._stop_unconfirmed = True
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
        """Everything to send at `now`: expiries, stops, resends, queued commands, polls."""
        if not self._velocity.is_zero and now >= self._velocity_expires_at - TIME_TOLERANCE_SECONDS:
            self.stop(now)  # the dead-man: nobody renewed the velocity
        outgoing: list[Outgoing] = []
        resend_due = (
            now - self._last_motion_sent_at >= self.resend_interval - TIME_TOLERANCE_SECONDS
        )
        if self._stop_unconfirmed and resend_due:
            outgoing += self._wrap_all(
                [(packets.pan_tilt_stop(), Purpose.STOP), (packets.zoom_stop(), Purpose.STOP)], now
            )
            self._last_motion_sent_at = now
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
        if now >= self._next_poll_at - TIME_TOLERANCE_SECONDS:
            self._next_poll_at = now + POSITION_POLL_SECONDS
            outgoing += self._wrap_all(
                [
                    (packets.pan_tilt_position_inquiry(), Purpose.PAN_TILT_INQUIRY),
                    (packets.zoom_position_inquiry(), Purpose.ZOOM_INQUIRY),
                ],
                now,
                payload_type=packets.PAYLOAD_INQUIRY,
            )
        return outgoing

    def received(self, datagram: bytes, now: float) -> None:
        """A datagram arrived from the camera; match it to what we sent."""
        reply = packets.parse_reply(datagram)
        if reply is None:
            return
        pending = self._pending.pop(reply.sequence, None)
        if pending is None:
            return  # the second answer (completion after ack), or a stale one
        self._missed_in_a_row = 0
        self._ever_answered = True
        self._went_offline_at = None
        if reply.kind == packets.ReplyKind.ERROR:
            self.last_error = f"camera refused a {pending.purpose.value} command"
        if pending.purpose == Purpose.STOP:
            self._stop_unconfirmed = False
        elif pending.purpose == Purpose.PAN_TILT_INQUIRY:
            self._raw_pan_tilt = packets.parse_pan_tilt_position(reply.payload)
        elif pending.purpose == Purpose.ZOOM_INQUIRY:
            self._raw_zoom = packets.parse_zoom_position(reply.payload)

    def expire_replies(self, now: float) -> None:
        """Count every packet whose reply is overdue as missed (call after `received`)."""
        for sequence, pending in list(self._pending.items()):
            if now - pending.sent_at >= REPLY_TIMEOUT_SECONDS - TIME_TOLERANCE_SECONDS:
                del self._pending[sequence]
                self._missed_in_a_row += 1
        if self.health == Status.OFFLINE and self._went_offline_at is None:
            self._went_offline_at = now

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
        if not self._velocity.is_zero:
            deadlines += [
                self._velocity_expires_at,
                self._last_motion_sent_at + self.resend_interval,
            ]
        if self._stop_unconfirmed:
            deadlines.append(self._last_motion_sent_at + self.resend_interval)
        deadlines += [pending.sent_at + REPLY_TIMEOUT_SECONDS for pending in self._pending.values()]
        return max(0.0, min(deadlines) - now)

    # --- what it observed -------------------------------------------------------------

    @property
    def health(self) -> Status:
        """offline until the first answer and after ten misses; degraded after three."""
        if not self._ever_answered or self._missed_in_a_row >= MISSED_FOR_OFFLINE:
            return Status.OFFLINE
        if self._missed_in_a_row >= MISSED_FOR_DEGRADED:
            return Status.DEGRADED
        return Status.OK

    @property
    def health_reason(self) -> str:
        """A sentence for the operator, empty when ok."""
        if not self._ever_answered:
            return "no answer from the camera yet"
        if self._missed_in_a_row >= MISSED_FOR_DEGRADED:
            return f"{self._missed_in_a_row} replies missed in a row"
        return ""

    @property
    def moving(self) -> bool:
        """True while a velocity is held."""
        return not self._velocity.is_zero

    @property
    def settled(self) -> bool:
        """At rest and no stop waiting for the camera's answer."""
        return self._velocity.is_zero and not self._stop_unconfirmed

    @property
    def velocity(self) -> Velocity:
        """The velocity being held (zero at rest)."""
        return self._velocity

    def position(self) -> Position | None:
        """The last position the camera reported, in degrees and 0..1 zoom."""
        if self._raw_pan_tilt is None:
            return None
        raw_pan, raw_tilt = self._raw_pan_tilt
        zoom = 0.0 if self._raw_zoom is None else self._raw_zoom / packets.ZOOM_POSITION_TELE
        return Position(
            pan=raw_pan * packets.DEGREES_PER_PAN_TILT_STEP,
            tilt=raw_tilt * packets.DEGREES_PER_PAN_TILT_STEP,
            zoom=min(1.0, max(0.0, zoom)),
        )

    def _wrap_all(
        self,
        items: list[tuple[bytes, Purpose]],
        now: float,
        payload_type: int = packets.PAYLOAD_COMMAND,
    ) -> list[Outgoing]:
        outgoing = []
        for payload, purpose in items:
            sequence = self._sequence.take()
            self._pending[sequence] = _Pending(purpose, now)
            outgoing.append(Outgoing(packets.wrap(payload, payload_type, sequence), purpose))
        return outgoing
