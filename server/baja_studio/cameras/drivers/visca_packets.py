"""VISCA-over-IP packets: builders, the sequence-numbered header, and reply parsers.

Pure data and functions, no sockets. Every byte here was ported from
`ptz_test.py` (March 2026), the bridge that drove the SMTAV at the last event;
where we deliberately differ from it, the comment says so and why.
"""

import math
from dataclasses import dataclass
from enum import Enum

from baja_studio.cameras.driver import Velocity

# Camera address 1: the SMTAV answers on it, and VISCA/IP has one camera per socket anyway.
CAMERA_ADDRESS = 1
COMMAND_HEADER = 0x80 | CAMERA_ADDRESS  # 0x81
# Replies come from address 8 + camera, in the high nibble: 0x90 for camera 1.
REPLY_HEADER = (CAMERA_ADDRESS + 8) << 4
TERMINATOR = 0xFF

# VISCA/IP payload types (bytes 0-1 of the 8-byte header).
PAYLOAD_COMMAND = 0x0100
PAYLOAD_INQUIRY = 0x0110
PAYLOAD_REPLY = 0x0111
IP_HEADER_LENGTH = 8
MAX_SEQUENCE = 0xFFFFFFFF

# Speed steps. The VISCA spec allows pan 0x01-0x18 and tilt 0x01-0x17; packets
# are clamped to that. ptz_test.py clamped to 18 / 17 decimal (0x12 / 0x11),
# probably hex read as decimal, so the top step actually used is a per-camera
# setting (limits.pan_speed_max_step / tilt_speed_max_step), probed on the bench.
PAN_SPEED_MIN, PAN_SPEED_MAX = 0x01, 0x18
TILT_SPEED_MIN, TILT_SPEED_MAX = 0x01, 0x17
# The settings' defaults: the spec's top pan step, and a tilt step below the
# spec's top (fast tilts overshoot on this class of camera; the bench decides).
DEFAULT_PAN_SPEED_MAX_STEP = 0x18
DEFAULT_TILT_SPEED_MAX_STEP = 0x14
ZOOM_SPEED_MIN, ZOOM_SPEED_MAX = 2, 7

# Direction codes in the pan-tilt drive command (ptz_test.py: 1=left 2=right 3=stop,
# 1=up 2=down 3=stop).
PAN_LEFT, PAN_RIGHT, PAN_STOP = 0x01, 0x02, 0x03
TILT_UP, TILT_DOWN, TILT_STOP = 0x01, 0x02, 0x03
ZOOM_TELE, ZOOM_WIDE = 0x20, 0x30  # ORed with the zoom speed 2-7
# Preset numbers are masked to 7 bits in the recall/store packets.
PRESET_MAX = 0x7F

# Position units. Not measured yet: ptz_test.py printed raw values only. These
# are the usual values for this class of camera; the bench checklist checks them.
DEGREES_PER_PAN_TILT_STEP = 0.075
ZOOM_POSITION_TELE = 0x4000


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def speed_step(magnitude: float, low: int, high: int) -> int:
    """Map |velocity| in (0, 1] onto a device speed step low..high (1.0 is the top step)."""
    # Half up, not Python's round-half-to-even, so equal steps of the stick give equal steps.
    return _clamp(low + math.floor(abs(magnitude) * (high - low) + 0.5), low, high)


def pan_tilt_stop() -> bytes:
    """Stop pan and tilt; byte-for-byte ptz_test.py's `cmd_stop` (speeds 0, both axes 3)."""
    return bytes([COMMAND_HEADER, 0x01, 0x06, 0x01, 0x00, 0x00, PAN_STOP, TILT_STOP, TERMINATOR])


def pan_tilt_drive(
    pan_speed: int, tilt_speed: int, pan_direction: int, tilt_direction: int
) -> bytes:
    """Pan-tilt drive with speed steps and direction codes (ptz_test.py's `cmd_pan_tilt`)."""
    return bytes(
        [
            COMMAND_HEADER,
            0x01,
            0x06,
            0x01,
            _clamp(pan_speed, PAN_SPEED_MIN, PAN_SPEED_MAX),
            _clamp(tilt_speed, TILT_SPEED_MIN, TILT_SPEED_MAX),
            pan_direction,
            tilt_direction,
            TERMINATOR,
        ]
    )


def zoom_tele(speed: int) -> bytes:
    """Zoom in at speed 2-7 (ptz_test.py's `cmd_zoom_in`)."""
    return bytes(
        [
            COMMAND_HEADER,
            0x01,
            0x04,
            0x07,
            ZOOM_TELE | _clamp(speed, ZOOM_SPEED_MIN, ZOOM_SPEED_MAX),
            TERMINATOR,
        ]
    )


def zoom_wide(speed: int) -> bytes:
    """Zoom out at speed 2-7 (ptz_test.py's `cmd_zoom_out`)."""
    return bytes(
        [
            COMMAND_HEADER,
            0x01,
            0x04,
            0x07,
            ZOOM_WIDE | _clamp(speed, ZOOM_SPEED_MIN, ZOOM_SPEED_MAX),
            TERMINATOR,
        ]
    )


def zoom_stop() -> bytes:
    """Stop zooming (ptz_test.py's `cmd_zoom_stop`)."""
    return bytes([COMMAND_HEADER, 0x01, 0x04, 0x07, 0x00, TERMINATOR])


def preset_recall(n: int) -> bytes:
    """Recall preset `n` (0-based; ptz_test.py masked it to 7 bits, so 0-127)."""
    return bytes([COMMAND_HEADER, 0x01, 0x04, 0x3F, 0x02, n & 0x7F, TERMINATOR])


def preset_store(n: int) -> bytes:
    """Store the current position as preset `n` (0-based, 0-127)."""
    return bytes([COMMAND_HEADER, 0x01, 0x04, 0x3F, 0x01, n & 0x7F, TERMINATOR])


def pan_tilt_position_inquiry() -> bytes:
    """Ask where pan and tilt are (ptz_test.py's `inq_pan_tilt_pos`)."""
    return bytes([COMMAND_HEADER, 0x09, 0x06, 0x12, TERMINATOR])


def zoom_position_inquiry() -> bytes:
    """Ask where zoom is (ptz_test.py's `inq_zoom_pos`)."""
    return bytes([COMMAND_HEADER, 0x09, 0x04, 0x47, TERMINATOR])


def pan_tilt_for(
    velocity: Velocity,
    pan_max_step: int = DEFAULT_PAN_SPEED_MAX_STEP,
    tilt_max_step: int = DEFAULT_TILT_SPEED_MAX_STEP,
) -> bytes:
    """The pan-tilt packet for a normalised velocity: a drive, or the stop if both are 0.

    A velocity of 1.0 maps to the camera's top step (`pan_max_step`, `tilt_max_step`).
    """
    if velocity.pan == 0.0 and velocity.tilt == 0.0:
        return pan_tilt_stop()
    pan_direction = PAN_STOP if velocity.pan == 0 else (PAN_RIGHT if velocity.pan > 0 else PAN_LEFT)
    tilt_direction = (
        TILT_STOP if velocity.tilt == 0 else (TILT_UP if velocity.tilt > 0 else TILT_DOWN)
    )
    return pan_tilt_drive(
        speed_step(velocity.pan, PAN_SPEED_MIN, min(pan_max_step, PAN_SPEED_MAX)),
        speed_step(velocity.tilt, TILT_SPEED_MIN, min(tilt_max_step, TILT_SPEED_MAX)),
        pan_direction,
        tilt_direction,
    )


def zoom_for(velocity: Velocity) -> bytes:
    """The zoom packet for a normalised velocity: tele, wide, or stop."""
    if velocity.zoom == 0.0:
        return zoom_stop()
    speed = speed_step(velocity.zoom, ZOOM_SPEED_MIN, ZOOM_SPEED_MAX)
    return zoom_tele(speed) if velocity.zoom > 0 else zoom_wide(speed)


# --- the VISCA/IP header ------------------------------------------------------------


class SequenceCounter:
    """VISCA/IP sequence numbers: start at 1, +1 per packet, wrap at 32 bits.

    ptz_test.py never sent the "reset sequence number" control command and the
    SMTAV accepted that, so neither do we.
    """

    def __init__(self, first: int = 1) -> None:
        """Start counting at `first`."""
        self._next = first

    def take(self) -> int:
        """The number for the packet about to be sent."""
        number = self._next
        self._next = (self._next + 1) & MAX_SEQUENCE
        return number


def wrap(payload: bytes, payload_type: int, sequence: int) -> bytes:
    """Prefix a VISCA payload with the 8-byte VISCA/IP header (ptz_test.py's `_wrap`)."""
    return (
        payload_type.to_bytes(2, "big")
        + len(payload).to_bytes(2, "big")
        + sequence.to_bytes(4, "big")
        + payload
    )


class ReplyKind(Enum):
    """What a camera's answer says."""

    ACK = "ack"  # 90 4y FF: command accepted
    COMPLETION = "completion"  # 90 5y FF: command done
    INQUIRY_ANSWER = "inquiry_answer"  # 90 50 ... FF with data
    ERROR = "error"  # 90 6y ee FF
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Reply:
    """One datagram from the camera, split into header fields and VISCA payload."""

    payload_type: int
    sequence: int
    payload: bytes

    @property
    def kind(self) -> ReplyKind:
        """Classify the VISCA payload by its second byte."""
        if len(self.payload) < 3 or self.payload[0] != REPLY_HEADER:
            return ReplyKind.UNKNOWN
        high = self.payload[1] & 0xF0
        if high == 0x40:
            return ReplyKind.ACK
        if high == 0x50:
            return ReplyKind.COMPLETION if len(self.payload) == 3 else ReplyKind.INQUIRY_ANSWER
        if high == 0x60:
            return ReplyKind.ERROR
        return ReplyKind.UNKNOWN


def parse_reply(datagram: bytes) -> Reply | None:
    """Split a datagram into a `Reply`, or None if it is too short to be VISCA/IP."""
    if len(datagram) < IP_HEADER_LENGTH + 3:
        return None
    payload_length = int.from_bytes(datagram[2:4], "big")
    payload = datagram[IP_HEADER_LENGTH : IP_HEADER_LENGTH + payload_length]
    return Reply(
        payload_type=int.from_bytes(datagram[0:2], "big"),
        sequence=int.from_bytes(datagram[4:8], "big"),
        payload=payload,
    )


def _nibbles(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value << 4) | (byte & 0x0F)
    return value


def _signed_16(value: int) -> int:
    # ptz_test.py used `> 0x8000`, which reads 0x8000 itself as positive; it is negative.
    return value - 0x10000 if value >= 0x8000 else value


def parse_pan_tilt_position(payload: bytes) -> tuple[int, int] | None:
    """Raw signed (pan, tilt) steps from `90 50 0p 0p 0p 0p 0t 0t 0t 0t FF`, or None.

    ptz_test.py's `parse_pan_tilt`, checking the full length it indexes into.
    """
    if len(payload) != 11 or payload[0] != REPLY_HEADER or payload[1] != 0x50:
        return None
    return _signed_16(_nibbles(payload[2:6])), _signed_16(_nibbles(payload[6:10]))


def parse_zoom_position(payload: bytes) -> int | None:
    """Raw zoom position from `90 50 0p 0p 0p 0p FF`, or None (ptz_test.py's `parse_zoom`)."""
    if len(payload) != 7 or payload[0] != REPLY_HEADER or payload[1] != 0x50:
        return None
    return _nibbles(payload[2:6])


def encode_nibbles(value: int, count: int) -> bytes:
    """`value` as `count` bytes of 0x0n nibbles, most significant first (used by the fake)."""
    return bytes((value >> (4 * shift)) & 0x0F for shift in reversed(range(count)))
