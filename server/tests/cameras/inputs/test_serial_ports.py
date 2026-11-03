"""Tests for the serial port listing (cameras/inputs/serial_ports.py).

The real list comes from pyserial's `list_ports.comports()`, which reads the
computer's devices. Here it is replaced (with `unittest.mock.patch`, the one
place this file needs it) by two ports in the wrong order.
"""

from types import SimpleNamespace
from unittest.mock import patch

from baja_studio.cameras.inputs.serial_ports import SerialPortInfo, list_serial_ports


def test_ports_are_listed_by_name_with_their_description_and_hardware_id() -> None:
    found = [
        SimpleNamespace(
            device="COM8", description="Silicon Labs CP210x", hwid="USB VID:PID=10C4:EA60"
        ),
        SimpleNamespace(device="COM3", description=None, hwid=None),
    ]

    with patch("serial.tools.list_ports.comports", return_value=found):
        ports = list_serial_ports()

    # Sorted, and a port the system says nothing about gets empty strings, not None.
    assert ports == [
        SerialPortInfo("COM3", "", ""),
        SerialPortInfo("COM8", "Silicon Labs CP210x", "USB VID:PID=10C4:EA60"),
    ]
