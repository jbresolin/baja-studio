"""The images OBS sends back from `GetSourceScreenshot`: data URIs, formats and sizes.

OBS answers a screenshot with `data:image/<format>;base64,<bytes>`, where
`<format>` is the `imageFormat` asked for (`jpg`, `png`, ...). The studio
needs the bytes (to serve a preview) and sometimes the picture's size in
pixels (to learn a source's size, `cameras/drivers/obs_crop_sync.py`), which
the PNG and JPEG headers carry. No image library is needed for either.
"""

import base64
import binascii
import struct
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_START = b"\xff\xd8"
# JPEG "start of frame" markers (baseline, progressive, ...) carry the size;
# 0xC4 (Huffman tables), 0xC8 and 0xCC are not frames.
JPEG_FRAME_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers with no length after them.
JPEG_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xD9)})
# Formats OBS names, as browsers expect them in a Content-Type.
MEDIA_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


class ImageDataError(ValueError):
    """The text is not an image data URI, or the image's size cannot be read."""


@dataclass(frozen=True)
class ImageData:
    """A decoded screenshot: its media type and bytes."""

    media_type: str
    content: bytes


def decode_data_uri(uri: str) -> ImageData:
    """Split `data:image/jpg;base64,...` into a media type and the decoded bytes."""
    header, separator, payload = uri.partition(",")
    if not separator or not header.startswith("data:") or not header.endswith(";base64"):
        raise ImageDataError("OBS did not send an image (not a base64 data URI)")
    declared = header[len("data:") : -len(";base64")]
    image_format = declared.split("/", 1)[-1].lower()
    try:
        content = base64.b64decode(payload, validate=True)
    except binascii.Error as error:
        raise ImageDataError(f"the image's base64 is broken: {error}") from None
    return ImageData(MEDIA_TYPES.get(image_format, declared or "application/octet-stream"), content)


def image_size(content: bytes) -> tuple[int, int]:
    """(width, height) in pixels of a PNG or JPEG image, read from its header."""
    if content.startswith(PNG_SIGNATURE):
        return _png_size(content)
    if content.startswith(JPEG_START):
        return _jpeg_size(content)
    raise ImageDataError("not a PNG or JPEG image")


def _png_size(content: bytes) -> tuple[int, int]:
    # The first chunk is always IHDR: length (4), "IHDR" (4), width (4), height (4).
    if len(content) < 24 or content[12:16] != b"IHDR":
        raise ImageDataError("PNG without its IHDR header")
    width, height = struct.unpack(">II", content[16:24])
    return int(width), int(height)


def _jpeg_size(content: bytes) -> tuple[int, int]:
    """Walk the JPEG's segments to the first frame header, which holds the size."""
    offset = 2
    while offset + 4 <= len(content):
        if content[offset] != 0xFF:
            raise ImageDataError("JPEG segment does not start with 0xFF")
        marker = content[offset + 1]
        if marker == 0xFF:  # padding before a marker
            offset += 1
            continue
        if marker in JPEG_STANDALONE_MARKERS:
            offset += 2
            continue
        (length,) = struct.unpack(">H", content[offset + 2 : offset + 4])
        if marker in JPEG_FRAME_MARKERS:
            # Length (2), precision (1), height (2), width (2).
            if offset + 9 > len(content):
                break
            height, width = struct.unpack(">HH", content[offset + 5 : offset + 9])
            return int(width), int(height)
        offset += 2 + length
    raise ImageDataError("JPEG without a frame header")
