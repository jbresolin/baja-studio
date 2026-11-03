"""Pelco-D frames from the RS485 joystick: byte parser and what each frame means.

Pure code, no serial port (that is `pelco_d_worker.py`). The flag map is the
one sniffed from the real controller with ptz_test.py in March 2026; keep it
exact, it is not quite the textbook Pelco-D (preset *recall* is 0x03 here).

A frame is 7 bytes: `FF addr cmd1 cmd2 data1 data2 checksum`, with
checksum = sum(bytes 1-5) & 0xFF. data1 is the pan speed, data2 the tilt
speed (or a preset number).
"""

from dataclasses import dataclass

SYNC = 0xFF
FRAME_LENGTH = 7

# cmd2 flags, as sniffed (ptz_test.py, March 2026).
TILT_UP = 0x08
TILT_DOWN = 0x10
PAN_RIGHT = 0x02
PAN_LEFT = 0x04
ZOOM_IN = 0x40
ZOOM_OUT = 0x20
STOP = 0x00
# cmd2 values with bit 0 set are commands, not motion flags.
EXTENDED_COMMAND_BIT = 0x01
PRESET_RECALL = 0x03  # preset number in data2, 1-based on the controller
CALL_BUTTON = 0x07  # arrives from address 0x03, number in data2
CALL_BUTTON_ADDRESS = 0x03

# The controller's speed bytes go 0-0x3F (0x40 and above, "turbo", count as full speed).
MAX_SPEED_BYTE = 0x3F


def checksum(frame: bytes) -> int:
    """sum(bytes 1-5) & 0xFF, as the controller computes it."""
    return sum(frame[1:6]) & 0xFF


def build_frame(address: int, command2: int, data1: int = 0, data2: int = 0) -> bytes:
    """A valid frame (cmd1 = 0), for tests and fixtures."""
    body = bytes([SYNC, address, 0x00, command2, data1, data2])
    return body + bytes([checksum(body + b"\x00")])


@dataclass(frozen=True)
class Frame:
    """One frame that passed the checksum."""

    address: int
    command1: int
    command2: int
    data1: int
    data2: int


class FrameParser:
    """Turns a byte stream into frames: resyncs on 0xFF, drops bad checksums."""

    def __init__(self) -> None:
        """Start with an empty buffer."""
        self._buffer = bytearray()
        self.rejected_frames = 0
        self.skipped_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        """Add bytes from the port; return every complete, valid frame found."""
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            self._skip_to_sync()
            if len(self._buffer) < FRAME_LENGTH:
                return frames
            candidate = bytes(self._buffer[:FRAME_LENGTH])
            if checksum(candidate) != candidate[6]:
                # Not a frame after all (a 0xFF inside data, or line noise): drop
                # just this sync byte and look for the next one, so a real frame
                # starting inside the bad bytes is not lost.
                self.rejected_frames += 1
                del self._buffer[0]
                continue
            del self._buffer[:FRAME_LENGTH]
            frames.append(Frame(*candidate[1:6]))

    def _skip_to_sync(self) -> None:
        start = self._buffer.find(SYNC)
        if start == -1:
            self.skipped_bytes += len(self._buffer)
            self._buffer.clear()
        elif start > 0:
            self.skipped_bytes += start
            del self._buffer[:start]


# --- what a frame means -----------------------------------------------------------


@dataclass(frozen=True)
class Motion:
    """Move the camera at `address`: normalised pan/tilt from the speed bytes,
    zoom direction -1 (out), 0 or +1 (in); the controller sends no zoom speed."""

    address: int
    pan: float
    tilt: float
    zoom_direction: int


@dataclass(frozen=True)
class StopAll:
    """Stop the camera at `address` (pan, tilt and zoom)."""

    address: int


@dataclass(frozen=True)
class RecallPreset:
    """Recall preset `n` on the camera at `address`; `n` is 0-based (the controller's minus 1)."""

    address: int
    n: int


@dataclass(frozen=True)
class CallButton:
    """The controller's Call button with its number (a camera-select hook for WP3)."""

    n: int


@dataclass(frozen=True)
class Unknown:
    """A valid frame this controller is not known to send (logged, otherwise ignored)."""

    frame: Frame


Action = Motion | StopAll | RecallPreset | CallButton | Unknown


def speed(byte: int) -> float:
    """A speed byte 0-0x3F as 0..1."""
    return min(byte, MAX_SPEED_BYTE) / MAX_SPEED_BYTE


def interpret(frame: Frame) -> Action:
    """What the controller means by `frame`."""
    command = frame.command2
    if frame.address == CALL_BUTTON_ADDRESS and command == CALL_BUTTON:
        return CallButton(frame.data2)
    if command & EXTENDED_COMMAND_BIT:
        if command == PRESET_RECALL and frame.data2 >= 1:
            return RecallPreset(frame.address, frame.data2 - 1)
        return Unknown(frame)
    if command == STOP:
        return StopAll(frame.address)
    pan = speed(frame.data1) * _direction(command, PAN_RIGHT, PAN_LEFT)
    tilt = speed(frame.data2) * _direction(command, TILT_UP, TILT_DOWN)
    zoom = _direction(command, ZOOM_IN, ZOOM_OUT)
    if pan == 0 and tilt == 0 and zoom == 0:
        return StopAll(frame.address)  # direction flags with speed 0: the stick is at rest
    return Motion(frame.address, pan, tilt, zoom)


def _direction(command: int, positive_flag: int, negative_flag: int) -> int:
    """+1, -1 or 0 for one axis; both flags at once (never seen) cancel out."""
    return (1 if command & positive_flag else 0) - (1 if command & negative_flag else 0)
