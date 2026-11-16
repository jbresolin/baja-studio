"""Tests for listing cameras and their modes (node/video/capture_devices.py).

On Windows, ffmpeg lists DirectShow cameras as text; these tests read
sample listings. The samples follow the messages in ffmpeg's source
(`libavdevice/dshow.c`): ffmpeg 6 prefixes lines with `[dshow @ …]`,
later versions with `[in#0 @ …]`, and newer ones add colour details after
each mode. The bench tool prints a laptop's real listing (`--raw`) to
compare with.
"""

import sys
from pathlib import Path

import pytest

from baja_studio.node.video.capture_devices import (
    SYNTHETIC_DEVICES,
    CaptureDevice,
    CaptureMode,
    capture_modes,
    list_capture_devices,
    parse_device_list,
    parse_mode_list,
    raw_device_listing,
)

# Two identical webcams (same name, different USB paths) and a microphone.
DEVICE_LISTING_FFMPEG_6 = r"""
[dshow @ 0000020f1c3c8e40] "EMEET SmartCam S600" (video)
[dshow @ 0000020f1c3c8e40]   Alternative name "@device_pnp_\\?\usb#vid_328f&pid_00b8&mi_00#7&2a52a0a5&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[dshow @ 0000020f1c3c8e40] "EMEET SmartCam S600" (video)
[dshow @ 0000020f1c3c8e40]   Alternative name "@device_pnp_\\?\usb#vid_328f&pid_00b8&mi_00#7&1b0c33f1&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[dshow @ 0000020f1c3c8e40] "Microphone (EMEET SmartCam S600)" (audio)
[dshow @ 0000020f1c3c8e40]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{1}"
dummy: Immediate exit requested
"""  # noqa: E501

DEVICE_LISTING_FFMPEG_9 = r"""
[in#0 @ 000001d8a2b4c0c0] "Integrated Camera" (video)
[in#0 @ 000001d8a2b4c0c0]   Alternative name "@device_pnp_\\?\usb#vid_04f2&pid_b6d9&mi_00#6&3a1a2b&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[in#0 @ 000001d8a2b4c0c0] "OBS Virtual Camera" (none)
Error opening input file dummy.
"""  # noqa: E501

MODE_LISTING = """
[dshow @ 000001] DirectShow video device options (from video devices)
[dshow @ 000001]  Pin "Capture" (alternative pin name "0")
[dshow @ 000001]   vcodec=mjpeg  min s=3840x2160 fps=30 max s=3840x2160 fps=30
[dshow @ 000001]   vcodec=mjpeg  min s=3840x2160 fps=30 max s=3840x2160 fps=30 (tv, bt470bg/bt709/unknown, topleft)
[dshow @ 000001]   vcodec=mjpeg  min s=1920x1080 fps=5 max s=1920x1080 fps=60
[dshow @ 000001]   pixel_format=yuyv422  min s=640x480 fps=5 max s=640x480 fps=30
[dshow @ 000001]   pixel_format=nv12  min s=1920x1080 fps=5 max s=1920x1080 fps=5 (tv, bt709)
"""  # noqa: E501


def test_video_devices_are_listed_with_their_usb_paths() -> None:
    devices = parse_device_list(DEVICE_LISTING_FFMPEG_6)
    # The microphone is left out; the two cameras of the same model are told
    # apart by their alternative name (the USB path), which is what a pipeline uses.
    assert [device.name for device in devices] == ["EMEET SmartCam S600", "EMEET SmartCam S600"]
    assert devices[0].device != devices[1].device
    assert devices[0].device.startswith("@device_pnp_\\\\?\\usb#vid_328f&pid_00b8")
    assert devices[0].device.endswith(
        "7&2a52a0a5&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\\global"
    )


def test_the_newer_line_prefix_is_read_too_and_devices_without_video_are_skipped() -> None:
    devices = parse_device_list(DEVICE_LISTING_FFMPEG_9)
    assert [device.name for device in devices] == ["Integrated Camera"]


def test_a_device_without_an_alternative_name_is_used_by_its_name() -> None:
    assert CaptureDevice("USB Camera").device == "USB Camera"


def test_modes_are_listed_once_each_biggest_first() -> None:
    modes = parse_mode_list(MODE_LISTING)
    # The repeated 4K line (with colour details) counts once.
    assert [mode.describe() for mode in modes] == [
        "mjpeg 3840x2160 @ 30 fps",
        "mjpeg 1920x1080 @ 60 fps",
        "nv12 1920x1080 @ 5 fps",
        "yuyv422 640x480 @ 30 fps",
    ]
    assert modes[0].is_mjpeg and not modes[2].is_mjpeg
    assert modes[1] == CaptureMode("mjpeg", 1920, 1080, max_fps=60.0, min_fps=5.0)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows lists its real cameras")
def test_without_directshow_the_synthetic_cameras_are_offered() -> None:
    assert list_capture_devices() == SYNTHETIC_DEVICES
    modes = capture_modes(SYNTHETIC_DEVICES[0].device)
    assert modes[0].describe() == "mjpeg 3840x2160 @ 30 fps"


def test_a_missing_ffmpeg_lists_nothing_rather_than_failing(tmp_path: Path) -> None:
    assert raw_device_listing(str(tmp_path / "no-ffmpeg")) == ""


def test_on_a_linux_node_cameras_come_from_v4l2(monkeypatch: pytest.MonkeyPatch) -> None:
    # The NanoPi (ADR 0024): with V4L2 in use, the listing is the V4L2 one, in
    # the same types. The kernel is replaced by the fake V4L2 system.
    from baja_studio.fakes.fake_v4l2 import R3S_USB3_LINK, FakeV4l2System
    from baja_studio.node.video import capture_devices

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(capture_devices, "uses_v4l2", lambda: True)
    monkeypatch.setattr(capture_devices, "KernelV4l2System", FakeV4l2System)
    [device] = list_capture_devices("ffmpeg")
    assert device.device == R3S_USB3_LINK
    assert capture_modes(R3S_USB3_LINK, "ffmpeg")[0].describe() == "mjpeg 3840x2160 @ 30 fps"
