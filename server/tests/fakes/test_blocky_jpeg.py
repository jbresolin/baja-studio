"""Tests for the JPEG of flat 8x8 blocks (fakes/blocky_jpeg.py).

Fakes serve these as snapshots. A JPEG decoder is the only real judge of a
JPEG, so where ffmpeg is installed (CI installs it for the rig tests) one
test asks it to decode the test card and checks the colours of a few
pixels; the others check the file's structure without a decoder.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from baja_studio.fakes.blocky_jpeg import QUADRANT_COLOURS, card_jpeg, encode_blocks, jpeg_size


def test_a_picture_starts_and_ends_with_the_jpeg_markers() -> None:
    picture = encode_blocks(2, 1, lambda column, row: (255, 255, 255))

    assert picture.startswith(b"\xff\xd8")  # start of image
    assert picture.endswith(b"\xff\xd9")  # end of image


def test_its_size_is_whole_blocks_and_can_be_read_back() -> None:
    assert jpeg_size(encode_blocks(40, 22, lambda column, row: (0, 0, 0))) == (320, 176)
    assert jpeg_size(card_jpeg(640, 360)) == (640, 360)


def test_a_different_shade_gives_a_different_picture() -> None:
    assert card_jpeg(64, 32, shade=0) != card_jpeg(64, 32, shade=128)


def test_a_size_out_of_range_is_refused() -> None:
    with pytest.raises(ValueError):
        encode_blocks(0, 1, lambda column, row: (0, 0, 0))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg to decode the JPEG")
def test_ffmpeg_decodes_the_test_card_with_its_colours(tmp_path: Path) -> None:
    card = tmp_path / "card.jpg"
    card.write_bytes(card_jpeg(64, 64))

    # Decode to raw RGB pixels: 64 x 64 x 3 bytes.
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(card), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=True,
    ).stdout

    def pixel(x: int, y: int) -> tuple[int, ...]:
        offset = (y * 64 + x) * 3
        return tuple(raw[offset : offset + 3])

    def close(seen: tuple[int, ...], expected: tuple[int, int, int]) -> bool:
        # JPEG rounds colours a little on the way through YCbCr.
        return all(abs(a - b) <= 4 for a, b in zip(seen, expected, strict=True))

    # On a 64 x 64 card each quadrant is one 32-pixel square of the checkerboard:
    # top-right and bottom-left are the quadrant's bright colour, the other two
    # the dark version (a third of each value).
    red, green, blue = QUADRANT_COLOURS[0]
    assert close(pixel(8, 8), (red // 3, green // 3, blue // 3))
    assert close(pixel(40, 8), QUADRANT_COLOURS[1])
    assert close(pixel(8, 40), QUADRANT_COLOURS[2])
