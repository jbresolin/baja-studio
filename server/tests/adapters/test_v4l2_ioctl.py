"""Tests for the V4L2 ioctl layer (adapters/v4l2_ioctl.py) where it needs no camera.

The real kernel calls are the bench's (docs/bench/nanopi.md); the code that
lists cameras and reads controls is tested against the fake V4L2 system in
`tests/node/video/test_v4l2_devices.py` and `tests/childprocs/test_uvc_v4l2.py`.
Here: what a control's flags mean, which nodes count as webcams, and that
the real listing copes with a computer without cameras and with links that
are not cameras.
"""

import sys
from pathlib import Path

import pytest

from baja_studio.adapters.v4l2_ioctl import (
    CandidateNode,
    ControlInfo,
    DeviceNode,
    KernelV4l2System,
    camera_nodes,
)


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


# --- choosing the webcams among the capture nodes (found with a Logitech BRIO) ----------------
# The BRIO showed up four times on the NanoPi: udev made two links for each of
# its nodes, and it has a second (infrared) capture node. `camera_nodes`
# should give it once.

BRIO = "Logitech BRIO"
OLD_LINK = "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usb-0:1:1.0-video-index0"
NEW_LINK = "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usbv3-0:1:1.0-video-index0"
OLD_IR_LINK = "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usb-0:1:1.0-video-index2"


def candidate(link: str, node: str, sends_mjpeg: bool = True) -> CandidateNode:
    """One by-path link pointing at `node` (a /dev/videoN), as the kernel listing finds it."""
    return CandidateNode(DeviceNode(link, node, BRIO, (81, int(node[-1]))), sends_mjpeg)


def test_a_node_with_two_links_is_listed_once_under_the_first_link() -> None:
    # Both links point at /dev/video0: the camera is listed once. The first link in
    # sorted order wins, so the name saved in the settings stays the same every time.
    nodes = camera_nodes([candidate(NEW_LINK, "/dev/video0"), candidate(OLD_LINK, "/dev/video0")])
    assert [node.stable_path for node in nodes] == [OLD_LINK]


def test_a_capture_node_that_does_not_send_mjpeg_is_not_a_camera() -> None:
    # The BRIO's infrared node (grey 340x340) captures, but never MJPEG: left out.
    nodes = camera_nodes(
        [candidate(OLD_LINK, "/dev/video0"), candidate(OLD_IR_LINK, "/dev/video2", False)]
    )
    assert [node.node_path for node in nodes] == ["/dev/video0"]


def test_two_cameras_stay_two() -> None:
    # Two real cameras on two ports are two nodes: both are kept.
    second = OLD_LINK.replace("0:1:1.0", "0:1.2:1.0")
    nodes = camera_nodes([candidate(OLD_LINK, "/dev/video0"), candidate(second, "/dev/video4")])
    assert len(nodes) == 2
