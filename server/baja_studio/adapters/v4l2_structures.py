"""The kernel's V4L2 structures and request numbers, as ctypes (`include/uapi/linux/videodev2.h`).

Only what the camera node asks (ADR 0024): the device's capabilities, its
formats, sizes and frame intervals, and its controls and menus. The layouts
are the kernel's stable ABI, the same on arm64 and x86-64; a test checks
every size and request number against the header's values.
"""

import ctypes

# --- ioctl numbers ------------------------------------------------------------------------
# _IOC(direction, type, number, size) from include/uapi/asm-generic/ioctl.h.
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("V") << 8) | number


def _ior(number: int, structure: type[ctypes.Structure]) -> int:
    return _ioc(_IOC_READ, number, ctypes.sizeof(structure))


def _iowr(number: int, structure: type[ctypes.Structure]) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, number, ctypes.sizeof(structure))


# --- the kernel's structures (videodev2.h) --------------------------------------------------


class Capability(ctypes.Structure):
    _fields_ = (
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    )


class FormatDescription(ctypes.Structure):
    _fields_ = (
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("description", ctypes.c_char * 32),
        ("pixelformat", ctypes.c_uint32),
        ("mbus_code", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    )


class FrameSizeDiscrete(ctypes.Structure):
    _fields_ = (("width", ctypes.c_uint32), ("height", ctypes.c_uint32))


class FrameSizeStepwise(ctypes.Structure):
    _fields_ = (
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("step_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
        ("step_height", ctypes.c_uint32),
    )


class FrameSizeUnion(ctypes.Union):
    _fields_ = (("discrete", FrameSizeDiscrete), ("stepwise", FrameSizeStepwise))


class FrameSizeEnum(ctypes.Structure):
    _fields_ = (
        ("index", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("size", FrameSizeUnion),
        ("reserved", ctypes.c_uint32 * 2),
    )


class Fraction(ctypes.Structure):
    _fields_ = (("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32))


class FrameIntervalStepwise(ctypes.Structure):
    _fields_ = (("min", Fraction), ("max", Fraction), ("step", Fraction))


class FrameIntervalUnion(ctypes.Union):
    _fields_ = (("discrete", Fraction), ("stepwise", FrameIntervalStepwise))


class FrameIntervalEnum(ctypes.Structure):
    _fields_ = (
        ("index", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("interval", FrameIntervalUnion),
        ("reserved", ctypes.c_uint32 * 2),
    )


class QueryControl(ctypes.Structure):
    _fields_ = (
        ("id", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
        ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    )


class QueryMenu(ctypes.Structure):
    # `struct v4l2_querymenu` is packed: the union (a 32-byte name or an s64) has no padding.
    _pack_ = 1
    _fields_ = (
        ("id", ctypes.c_uint32),
        ("index", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("reserved", ctypes.c_uint32),
    )


class Control(ctypes.Structure):
    _fields_ = (("id", ctypes.c_uint32), ("value", ctypes.c_int32))


VIDIOC_QUERYCAP = _ior(0, Capability)
VIDIOC_ENUM_FMT = _iowr(2, FormatDescription)
VIDIOC_G_CTRL = _iowr(27, Control)
VIDIOC_S_CTRL = _iowr(28, Control)
VIDIOC_QUERYCTRL = _iowr(36, QueryControl)
VIDIOC_QUERYMENU = _iowr(37, QueryMenu)
VIDIOC_ENUM_FRAMESIZES = _iowr(74, FrameSizeEnum)
VIDIOC_ENUM_FRAMEINTERVALS = _iowr(75, FrameIntervalEnum)

BUFFER_TYPE_VIDEO_CAPTURE = 1
CAP_VIDEO_CAPTURE = 0x00000001
CAP_DEVICE_CAPS = 0x80000000
FRAME_SIZE_DISCRETE = 1
FRAME_INTERVAL_DISCRETE = 1
CONTROL_TYPE_MENU = 3
CONTROL_FLAG_DISABLED = 0x0001


def fourcc(code: str) -> int:
    """`"MJPG"` -> the 32-bit pixel format number V4L2 uses."""
    a, b, c, d = (ord(character) for character in code)
    return a | (b << 8) | (c << 16) | (d << 24)


def fourcc_text(number: int) -> str:
    """The pixel format number -> `"MJPG"`."""
    return "".join(chr((number >> shift) & 0xFF) for shift in (0, 8, 16, 24))
