"""Tests for the fake serial port (fakes/fake_serial.py), which plays the RS485 adapter."""

import pytest
import serial

from baja_studio.cameras.inputs import pelco_d
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES, FakeSerialPorts


def test_bytes_fed_in_come_out_of_read_up_to_the_size_asked() -> None:
    port = FakeSerialPorts().add("COM8")
    port.mark_opened(2400)
    port.feed(b"\x01\x02\x03")

    assert port.read(2) == b"\x01\x02"
    assert port.read(32) == b"\x03"


def test_read_returns_nothing_after_its_timeout_when_nothing_arrived() -> None:
    # Like pyserial with a timeout: an empty result, not an error.
    port = FakeSerialPorts().add("COM8")
    port.mark_opened(2400)

    assert port.read(32) == b""


def test_an_unknown_or_unplugged_port_cannot_be_opened() -> None:
    ports = FakeSerialPorts()
    port = ports.add("COM8")
    port.unplug()

    with pytest.raises(serial.SerialException):
        ports.open("COM8", 2400)
    with pytest.raises(serial.SerialException):
        ports.open("COM9", 2400)


def test_reading_from_an_unplugged_port_fails_like_pyserial() -> None:
    ports = FakeSerialPorts()
    port = ports.add("COM8")
    ports.open("COM8", 2400)

    port.unplug()

    with pytest.raises(serial.SerialException):
        port.read(32)


def test_every_sniffed_fixture_is_a_valid_seven_byte_frame() -> None:
    for name, frame in SNIFFED_FRAMES.items():
        assert len(frame) == pelco_d.FRAME_LENGTH, name
        assert frame[0] == pelco_d.SYNC, name
        assert pelco_d.checksum(frame) == frame[6], name
