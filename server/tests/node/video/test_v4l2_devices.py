"""Tests for listing a Linux node's cameras and their modes (node/video/v4l2_devices.py).

On the NanoPi the cameras come from V4L2, not DirectShow. These tests use
the fake V4L2 system (`fakes/fake_v4l2.py`), which holds S600-like webcams
plugged into the R3S's USB port, and check that they come out in the same
form as the laptop's listing, so the node's page and the bench need no change.
"""

from baja_studio.adapters.v4l2_structures import fourcc
from baja_studio.fakes.fake_v4l2 import HUB_LINK_PATTERN, R3S_USB3_LINK, S600_NAME, FakeV4l2System
from baja_studio.node.video.capture_devices import CaptureMode
from baja_studio.node.video.v4l2_devices import ffmpeg_format_name, list_v4l2_devices, v4l2_modes


def test_each_camera_is_listed_by_its_name_and_its_usb_port_link() -> None:
    # Two identical S600s behind a hub: the same name, told apart by their port.
    second = HUB_LINK_PATTERN.format(port=2)
    system = FakeV4l2System([R3S_USB3_LINK, second])
    devices = list_v4l2_devices(system)
    assert [device.name for device in devices] == [S600_NAME, S600_NAME]
    # `device` is what a pipeline opens: the link, which stays the same for that port.
    assert {device.device for device in devices} == {R3S_USB3_LINK, second}


def test_an_unplugged_camera_is_not_listed() -> None:
    system = FakeV4l2System()
    system.unplug(R3S_USB3_LINK)
    assert list_v4l2_devices(system) == []


def test_modes_come_biggest_and_fastest_first_with_ffmpeg_s_format_names() -> None:
    modes = v4l2_modes(R3S_USB3_LINK, FakeV4l2System())
    # 4K MJPEG at 30 fps (and 15) first: what the node wants.
    assert modes[0] == CaptureMode("mjpeg", 3840, 2160, max_fps=30.0, min_fps=15.0)
    assert CaptureMode("mjpeg", 1920, 1080, 60.0, 15.0) in modes
    # V4L2's "YUYV" is ffmpeg's "yuyv422"; raw formats are listed, but not MJPEG.
    assert CaptureMode("yuyv422", 640, 480, 30.0, 30.0) in modes
    assert modes[0].is_mjpeg and not modes[-1].is_mjpeg


def test_the_device_is_closed_after_its_modes_are_read() -> None:
    system = FakeV4l2System()
    v4l2_modes(R3S_USB3_LINK, system)
    assert system.opened and all(device.closed for device in system.opened)


def test_a_camera_that_cannot_be_opened_has_no_modes() -> None:
    assert v4l2_modes("/dev/v4l/by-path/nothing-there", FakeV4l2System()) == []


def test_format_names_follow_ffmpeg() -> None:
    assert ffmpeg_format_name(fourcc("MJPG")) == "mjpeg"
    assert ffmpeg_format_name(fourcc("NV12")) == "nv12"
    # An unknown code is passed on in lower case rather than dropped.
    assert ffmpeg_format_name(fourcc("ABCD")) == "abcd"
