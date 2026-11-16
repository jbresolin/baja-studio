"""Tests for the kernel's V4L2 structures and request numbers (adapters/v4l2_structures.py).

The node on a Linux board asks the kernel about its cameras with "ioctls":
numbered requests that pass a C structure back and forth. If a structure's
size or a request's number is wrong, the kernel refuses every question, so
these tests compare them with the values the kernel's own header gives
(`videodev2.h`, the same on arm64 and x86-64). No camera is needed.
"""

import ctypes

from baja_studio.adapters import v4l2_structures
from baja_studio.adapters.v4l2_structures import (
    VIDIOC_ENUM_FMT,
    VIDIOC_ENUM_FRAMEINTERVALS,
    VIDIOC_ENUM_FRAMESIZES,
    VIDIOC_G_CTRL,
    VIDIOC_QUERYCAP,
    VIDIOC_QUERYCTRL,
    VIDIOC_QUERYMENU,
    VIDIOC_S_CTRL,
    fourcc,
    fourcc_text,
)


def test_the_request_numbers_are_the_kernel_s() -> None:
    # The values `videodev2.h` gives on Linux (worked out from its _IOR/_IOWR macros).
    assert VIDIOC_QUERYCAP == 0x80685600
    assert VIDIOC_ENUM_FMT == 0xC0405602
    assert VIDIOC_G_CTRL == 0xC008561B
    assert VIDIOC_S_CTRL == 0xC008561C
    assert VIDIOC_QUERYCTRL == 0xC0445624
    assert VIDIOC_QUERYMENU == 0xC02C5625
    assert VIDIOC_ENUM_FRAMESIZES == 0xC02C564A
    assert VIDIOC_ENUM_FRAMEINTERVALS == 0xC034564B


def test_the_structures_have_the_kernel_s_sizes() -> None:
    # The request numbers above contain these sizes; checked on their own here too,
    # so a wrong field shows as a wrong size, not only as a wrong number.
    sizes = {
        "Capability": 104,
        "FormatDescription": 64,
        "FrameSizeEnum": 44,
        "FrameIntervalEnum": 52,
        "QueryControl": 68,
        "QueryMenu": 44,  # packed in the kernel: no padding after its 32-byte name
        "Control": 8,
    }
    for name, size in sizes.items():
        assert ctypes.sizeof(getattr(v4l2_structures, name)) == size, name


def test_pixel_format_codes_go_both_ways() -> None:
    # "MJPG" is stored as four bytes, the first letter in the lowest byte.
    assert fourcc("MJPG") == 0x47504A4D
    assert fourcc_text(fourcc("YUYV")) == "YUYV"
