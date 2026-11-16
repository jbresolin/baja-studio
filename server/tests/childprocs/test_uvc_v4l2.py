"""Tests for the UVC child's Linux backend (childprocs/uvc_v4l2.py) with the fake V4L2 system.

On the NanoPi the studio's Image page changes the webcam's exposure, white
balance and focus through V4L2. The fake system (`fakes/fake_v4l2.py`)
behaves like the Linux webcam driver: a value whose "auto" is on cannot be
written, and a replugged camera comes back with new device nodes and its
factory settings. These tests check that the backend copes with both, and
that exposure is given in the same units as on Windows.
"""

import pytest

from baja_studio.adapters.v4l2_ioctl import ControlInfo
from baja_studio.childprocs import uvc_v4l2
from baja_studio.childprocs.uvc_duvc import DeviceMissingError, UvcError
from baja_studio.childprocs.uvc_v4l2 import (
    V4l2Backend,
    exposure_from_log2,
    exposure_range,
    exposure_to_log2,
)
from baja_studio.fakes.fake_v4l2 import R3S_USB3_LINK, S600_NAME, FakeV4l2System

# V4L2's exposure control as the fake S600 has it: 1..5000 steps of 100 µs.
EXPOSURE = ControlInfo(uvc_v4l2.CID_EXPOSURE_ABSOLUTE, "Exposure", 1, 1, 5000, 1, 156, 0)


def backend_with_camera() -> tuple[V4l2Backend, FakeV4l2System]:
    """A backend over one fake S600 in the R3S's USB 3 port, already listed once."""
    system = FakeV4l2System()
    backend = V4l2Backend(system)
    backend.list_devices()
    return backend, system


def test_the_camera_is_listed_by_name_and_port_link() -> None:
    backend, _ = backend_with_camera()
    [device] = backend.list_devices()
    assert (device.name, device.path, device.index) == (S600_NAME, R3S_USB3_LINK, 0)


def test_the_controls_it_has_are_given_by_the_studio_s_names() -> None:
    backend, _ = backend_with_camera()
    supported = backend.supported(R3S_USB3_LINK)
    # The S600 has no motor: pan, tilt and zoom are not listed; the image controls are.
    assert "exposure" in supported and "white_balance" in supported and "focus" in supported
    assert not {"pan", "tilt", "zoom"} & set(supported)


def test_exposure_is_read_in_windows_units_whole_stops_of_log2_seconds() -> None:
    backend, _ = backend_with_camera()
    exposure = backend.read(R3S_USB3_LINK, "exposure")
    # 156 x 100 µs = 15.6 ms, about 1/64 s: -6 in DirectShow's units.
    assert exposure["value"] == -6
    # 100 µs .. 0.5 s becomes -13 .. -1, one stop a step, as a laptop shows it.
    assert (exposure["min"], exposure["max"], exposure["step"]) == (-13, -1, 1)
    # The camera starts on automatic exposure ("aperture priority" in V4L2's menu).
    assert exposure["auto"] is True


def test_exposure_units_convert_both_ways_and_stay_in_range() -> None:
    assert exposure_to_log2(156) == -6
    # -5 is 1/32 s = 312.5 steps of 100 µs, rounded to 312.
    assert exposure_from_log2(-5, EXPOSURE) == 312
    # Beyond the range: clamped to the control's own limits.
    assert exposure_from_log2(3, EXPOSURE) == 5000
    assert exposure_from_log2(-20, EXPOSURE) == 1
    assert exposure_range(EXPOSURE) == (-13, -1)


def test_writing_a_manual_value_turns_auto_off_first_then_writes_the_value() -> None:
    backend, system = backend_with_camera()
    camera = system.cameras[R3S_USB3_LINK]
    backend.write(R3S_USB3_LINK, "white_balance", 5000, auto=False)
    # Order matters: the driver refuses the temperature while automatic white balance is on.
    assert camera.writes == [
        (uvc_v4l2.CID_AUTO_WHITE_BALANCE, 0),
        (uvc_v4l2.CID_WHITE_BALANCE_TEMPERATURE, 5000),
    ]
    reading = backend.read(R3S_USB3_LINK, "white_balance")
    assert (reading["value"], reading["auto"]) == (5000, False)


def test_writing_manual_exposure_sends_the_v4l2_steps_for_that_stop() -> None:
    backend, system = backend_with_camera()
    backend.write(R3S_USB3_LINK, "exposure", -5, auto=False)
    assert system.cameras[R3S_USB3_LINK].writes == [
        (uvc_v4l2.CID_EXPOSURE_AUTO, uvc_v4l2.EXPOSURE_MANUAL),
        (uvc_v4l2.CID_EXPOSURE_ABSOLUTE, 312),
    ]
    assert backend.read(R3S_USB3_LINK, "exposure")["value"] == -5


def test_turning_auto_on_writes_only_the_auto_switch() -> None:
    backend, system = backend_with_camera()
    backend.write(R3S_USB3_LINK, "focus", 100, auto=False)
    system.cameras[R3S_USB3_LINK].writes.clear()
    backend.write(R3S_USB3_LINK, "focus", 100, auto=True)
    # The value is not written: the camera would refuse it, and on auto it decides anyway.
    assert system.cameras[R3S_USB3_LINK].writes == [(uvc_v4l2.CID_FOCUS_AUTO, 1)]


def test_exposure_auto_is_the_aperture_priority_entry_of_its_menu() -> None:
    backend, system = backend_with_camera()
    backend.write(R3S_USB3_LINK, "exposure", -5, auto=False)
    backend.write(R3S_USB3_LINK, "exposure", 0, auto=True)
    assert system.cameras[R3S_USB3_LINK].writes[-1] == (
        uvc_v4l2.CID_EXPOSURE_AUTO,
        uvc_v4l2.EXPOSURE_APERTURE_PRIORITY,
    )


def test_auto_on_a_control_without_an_automatic_mode_is_refused() -> None:
    backend, _ = backend_with_camera()
    with pytest.raises(UvcError, match="no automatic mode"):
        backend.write(R3S_USB3_LINK, "brightness", 0, auto=True)


def test_a_control_the_camera_lacks_is_refused_by_name() -> None:
    backend, _ = backend_with_camera()
    with pytest.raises(UvcError, match="no pan control"):
        backend.read(R3S_USB3_LINK, "pan")


def test_an_unplugged_camera_is_reported_missing() -> None:
    backend, system = backend_with_camera()
    system.unplug(R3S_USB3_LINK)
    with pytest.raises(DeviceMissingError):
        backend.read(R3S_USB3_LINK, "gain")


def test_a_replug_is_reported_once_so_the_service_writes_the_settings_again() -> None:
    backend, system = backend_with_camera()
    # Unplugged and plugged back between two of the service's 2 s looks:
    # same port link, new device node.
    system.unplug(R3S_USB3_LINK)
    system.plug(R3S_USB3_LINK)
    backend.probe()
    assert backend.take_reopened() == [R3S_USB3_LINK]
    # Reported once: the next look finds nothing new.
    backend.probe()
    assert backend.take_reopened() == []


def test_every_device_opened_is_closed_again() -> None:
    backend, system = backend_with_camera()
    backend.read(R3S_USB3_LINK, "exposure")
    backend.write(R3S_USB3_LINK, "gain", 10, auto=False)
    # Nothing is kept open between calls, so an unplug never leaves a dead handle.
    assert system.opened and all(device.closed for device in system.opened)
