"""Tests for the icon file and the status dot (icons.py)."""

from pathlib import Path

from PIL import Image

from baja_studio_tray.icons import (
    ALL_SIZES,
    BADGE_COLOURS,
    FULL_ARTWORK_SIZES,
    ICON_PATH,
    SILHOUETTE_SIZES,
    badged_frames,
    load_frames,
    save_ico,
    with_badge,
)
from baja_studio_tray.status import Badge


def pixels(image: Image.Image) -> list[bytes]:
    """Every pixel as 4 bytes: red, green, blue, opacity."""
    data = image.convert("RGBA").tobytes()
    return [data[start : start + 4] for start in range(0, len(data), 4)]


def pixel_at(image: Image.Image, x: int, y: int) -> bytes:
    return pixels(image)[y * image.width + x]


def colour_count(image: Image.Image) -> int:
    """How many different colours the fully opaque pixels use."""
    return len({pixel[:3] for pixel in pixels(image) if pixel[3] == 255})


def test_the_icon_file_has_every_size_the_tray_and_installer_need() -> None:
    # The file is committed (made by installer/icons/make_icons.py) and shipped
    # in the package, so the tray never needs the SVG.
    assert ICON_PATH.is_file()

    frames = load_frames()

    assert sorted(frames) == sorted(ALL_SIZES)
    for size, frame in frames.items():
        assert frame.size == (size, size)


def test_the_small_frames_are_a_single_fill_silhouette() -> None:
    frames = load_frames()

    # At tray size one colour (edges are only partly opaque, so not counted);
    # the large frames carry the full, many-coloured artwork.
    for size in SILHOUETTE_SIZES:
        assert colour_count(frames[size]) == 1, size
    for size in FULL_ARTWORK_SIZES:
        assert colour_count(frames[size]) > 1, size


def test_the_status_dot_sits_bottom_right_in_the_badge_colour() -> None:
    frame = Image.new("RGBA", (16, 16))

    badged = with_badge(frame, Badge.RED)

    # The dot's centre is the badge colour; the top-left corner is untouched.
    assert pixel_at(badged, 12, 12) == bytes((*BADGE_COLOURS[Badge.RED], 255))
    assert pixel_at(badged, 0, 0) == bytes(4)
    # The frame given is not changed: every poll starts from the clean artwork.
    assert pixel_at(frame, 12, 12) == bytes(4)


def test_a_badged_ico_keeps_each_frame_rather_than_scaling_the_largest(tmp_path: Path) -> None:
    frames = badged_frames(load_frames(), Badge.GREEN)
    target = tmp_path / "green.ico"

    save_ico(frames, target)
    reloaded = load_frames(target)

    # The 16 px frame read back is the silhouette with its dot, not a
    # scaled-down copy of the 256 px artwork.
    assert sorted(reloaded) == sorted(ALL_SIZES)
    assert reloaded[16].tobytes() == frames[16].tobytes()
