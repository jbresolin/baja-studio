"""An in-memory serial port standing in for the RS485 adapter, and real sniffed frames.

`FakeSerialPort` behaves like `serial.Serial` with a read timeout: `read`
returns what has been fed, or nothing after the timeout. `unplug()` makes
the next read fail the way pyserial does when the USB adapter is pulled, and
opening it fails while it is unplugged. `FakeSerialPorts` plays the
computer's set of ports, so tests can hand the controller worker an opener.

`SNIFFED_FRAMES` are the controller's frames, with the flags mapped from the
real controller with ptz_test.py in March 2026 (address 1; preset 1; the
Call button on address 3). ptz_test.py logged the flags, not raw captures,
so each frame is rebuilt from them with the controller's checksum.
"""

import threading
from collections import deque

import serial

from baja_studio.cameras.inputs import pelco_d

# Full deflection on the controller is speed byte 0x3F; half is 0x20.
FULL, HALF = 0x3F, 0x20

SNIFFED_FRAMES: dict[str, bytes] = {
    "stop": pelco_d.build_frame(1, pelco_d.STOP),
    "tilt_up_full": pelco_d.build_frame(1, pelco_d.TILT_UP, 0x00, FULL),
    "tilt_down_full": pelco_d.build_frame(1, pelco_d.TILT_DOWN, 0x00, FULL),
    "pan_right_full": pelco_d.build_frame(1, pelco_d.PAN_RIGHT, FULL, 0x00),
    "pan_right_half": pelco_d.build_frame(1, pelco_d.PAN_RIGHT, HALF, 0x00),
    "pan_left_full": pelco_d.build_frame(1, pelco_d.PAN_LEFT, FULL, 0x00),
    "up_left_full": pelco_d.build_frame(1, pelco_d.TILT_UP | pelco_d.PAN_LEFT, FULL, FULL),
    "up_right_full": pelco_d.build_frame(1, pelco_d.TILT_UP | pelco_d.PAN_RIGHT, FULL, FULL),
    "down_left_full": pelco_d.build_frame(1, pelco_d.TILT_DOWN | pelco_d.PAN_LEFT, FULL, FULL),
    "down_right_full": pelco_d.build_frame(1, pelco_d.TILT_DOWN | pelco_d.PAN_RIGHT, FULL, FULL),
    "zoom_in": pelco_d.build_frame(1, pelco_d.ZOOM_IN),
    "zoom_out": pelco_d.build_frame(1, pelco_d.ZOOM_OUT),
    "recall_preset_1": pelco_d.build_frame(1, pelco_d.PRESET_RECALL, 0x00, 0x01),
    "call_2": pelco_d.build_frame(pelco_d.CALL_BUTTON_ADDRESS, pelco_d.CALL_BUTTON, 0x00, 0x02),
}


class FakeSerialPort:
    """One fake port; the test feeds bytes in, the worker reads them out."""

    def __init__(self, name: str, read_timeout_seconds: float = 0.02) -> None:
        """`read_timeout_seconds` is real time, like pyserial's: how long `read` waits."""
        self.name = name
        self.read_timeout_seconds = read_timeout_seconds
        self._incoming: deque[int] = deque()
        self._condition = threading.Condition()
        self.plugged_in = True
        self.is_open = False
        self.open_count = 0
        self.baud: int | None = None

    def feed(self, data: bytes) -> None:
        """Bytes arriving from the controller."""
        with self._condition:
            self._incoming.extend(data)
            self._condition.notify_all()

    def unplug(self) -> None:
        """Pull the USB adapter: reads fail, and so does opening."""
        with self._condition:
            self.plugged_in = False
            self._incoming.clear()
            self._condition.notify_all()

    def plug_in(self) -> None:
        """Put it back; the worker must reopen it."""
        with self._condition:
            self.plugged_in = True

    def read(self, size: int) -> bytes:
        """Up to `size` bytes, waiting at most the read timeout, like pyserial."""
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._incoming) or not self.plugged_in or not self.is_open,
                timeout=self.read_timeout_seconds,
            )
            if not self.plugged_in or not self.is_open:
                raise serial.SerialException(f"{self.name}: device reports readiness but no data")
            count = min(size, len(self._incoming))
            return bytes(self._incoming.popleft() for _ in range(count))

    def mark_opened(self, baud: int) -> None:
        """Called by `FakeSerialPorts.open`."""
        with self._condition:
            self.is_open = True
            self.open_count += 1
            self.baud = baud

    def close(self) -> None:
        """Close the port (the worker does this before reopening)."""
        with self._condition:
            self.is_open = False
            self._condition.notify_all()


class FakeSerialPorts:
    """The computer's serial ports, by name; `open` is the worker's opener."""

    def __init__(self) -> None:
        """No ports until a test adds them."""
        self._ports: dict[str, FakeSerialPort] = {}

    def add(self, name: str) -> FakeSerialPort:
        """A new port called `name` (e.g. "COM8")."""
        port = FakeSerialPort(name)
        self._ports[name] = port
        return port

    def open(self, name: str, baud: int) -> FakeSerialPort:
        """Open like `serial.Serial(name, baud)`: fails for unknown or unplugged ports."""
        port = self._ports.get(name)
        if port is None or not port.plugged_in:
            raise serial.SerialException(f"could not open port {name!r}: FileNotFoundError")
        port.mark_opened(baud)
        return port
