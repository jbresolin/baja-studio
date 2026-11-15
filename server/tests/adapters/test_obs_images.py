"""Tests for reading OBS screenshots (adapters/obs_images.py).

OBS sends a screenshot as text: `data:image/jpg;base64,<the image>`. The
studio needs the image's bytes (to serve a preview) and sometimes its size
in pixels (to learn how big a camera's picture is). The size is written in
the first bytes of a PNG or JPEG file, so we build tiny files by hand here
and check that it is read back right.
"""

import base64
import struct

import pytest

from baja_studio.adapters.obs_images import ImageDataError, decode_data_uri, image_size


def png_header(width: int, height: int) -> bytes:
    """The first bytes of a PNG: the signature, then the IHDR chunk with the size."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\0\0\0\0"


def jpeg_header(width: int, height: int) -> bytes:
    """A JPEG start: SOI, an APP0 segment to skip, then a baseline frame header (SOF0)."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_a_data_uri_gives_back_the_bytes_and_a_browser_media_type() -> None:
    # OBS says "jpg"; browsers expect "image/jpeg" in a Content-Type.
    image = decode_data_uri("data:image/jpg;base64," + base64.b64encode(b"abc").decode())

    assert image.content == b"abc"
    assert image.media_type == "image/jpeg"


def test_text_that_is_not_a_base64_data_uri_is_refused() -> None:
    with pytest.raises(ImageDataError, match="not a base64 data URI"):
        decode_data_uri("hello")
    with pytest.raises(ImageDataError, match="base64 is broken"):
        decode_data_uri("data:image/png;base64,***")


def test_the_size_of_a_png_is_read_from_its_header() -> None:
    assert image_size(png_header(1920, 1080)) == (1920, 1080)


def test_the_size_of_a_jpeg_is_read_from_its_frame_header_after_other_segments() -> None:
    # The APP0 segment comes first and must be skipped by its length.
    assert image_size(jpeg_header(3840, 2160)) == (3840, 2160)


def test_bytes_that_are_neither_png_nor_jpeg_are_refused() -> None:
    with pytest.raises(ImageDataError, match="not a PNG or JPEG"):
        image_size(b"GIF89a")
    with pytest.raises(ImageDataError, match="without a frame header"):
        image_size(b"\xff\xd8\xff\xd9")
