"""Tests for the V4L2 ioctl layer (adapters/v4l2_ioctl.py) where it needs no camera.

The real kernel calls are the bench's (docs/bench/nanopi.md); the code that
lists cameras and reads controls is tested against the fake V4L2 system in
`tests/node/video/test_v4l2_devices.py` and `tests/childprocs/test_uvc_v4l2.py`.
Here: what a control's flags mean, and that the real listing copes with a
computer without cameras and with links that are not cameras.
"""

import sys
from pathlib import Path

import pytest

from baja_studio.adapters.v4l2_ioctl import ControlInfo, KernelV4l2System


def test_a_disabled_control_is_seen_as_disabled() -> None:
    control = ControlInfo(1, "Gain", 1, 0, 100, 1, 0, flags=0x0001)
    assert control.disabled
    assert not ControlInfo(1, "Gain", 1, 0, 100, 1, 0, flags=0x0010).disabled


def test_without_a_by_path_folder_there_are_no_cameras(tmp_path: Path) -> None:
    # A computer with no webcam has no /dev/v4l at all.
    assert KernelV4l2System(tmp_path / "missing").capture_nodes() == []


# Linux only: udev's link names contain ":", which Windows does not allow in a file name,
# and this listing only ever runs on a Linux node.
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="V4L2 links exist on Linux only")
def test_a_link_to_something_that_is_not_a_camera_is_skipped(tmp_path: Path) -> None:
    # A by-path link pointing at an ordinary file: the kernel answers "not a
    # video device" to the first ioctl, and the listing simply leaves it out.
    not_a_camera = tmp_path / "file"
    not_a_camera.write_bytes(b"")
    links = tmp_path / "by-path"
    links.mkdir()
    (links / "usb-0:1:1.0-video-index0").symlink_to(not_a_camera)
    assert KernelV4l2System(links).capture_nodes() == []
