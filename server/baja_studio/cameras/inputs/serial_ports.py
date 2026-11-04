"""The computer's serial ports, for choosing a controller's port on the configuration page."""

from collections.abc import Callable
from dataclasses import dataclass

from serial.tools import list_ports


@dataclass(frozen=True)
class SerialPortInfo:
    """One port: its name (COM8, /dev/ttyUSB0) and what the system says it is.

    `hwid` carries the USB vendor and product ids, so the CP2102N adapter
    (VID:PID 10C4:EA60) can be told from other adapters.
    """

    device: str
    description: str
    hwid: str


SerialPortLister = Callable[[], list[SerialPortInfo]]


def list_serial_ports() -> list[SerialPortInfo]:
    """Every serial port the system knows of, sorted by name."""
    ports = [
        SerialPortInfo(port.device, port.description or "", port.hwid or "")
        for port in list_ports.comports()
    ]
    return sorted(ports, key=lambda port: port.device)
