"""Tests for the digital PTZ's geometry (cameras/crop_window.py).

A crop camera is a window moving over a fixed picture. These tests use a 4K
source (3840x2160) cut to 16:9 windows no narrower than half the width
(1920 px: a 1080p window, the lossless 2x zoom), and check the promises the
contract makes: zoom keeps the window's shape and centre, the window never
leaves the picture, and it never gets narrower than `min_window`.
"""

import pytest

from baja_studio.cameras.crop_window import (
    CropFrame,
    CropSpeeds,
    CropWindow,
    PixelCrop,
    WindowTarget,
)
from baja_studio.cameras.driver import Velocity

FOUR_K = CropFrame(source_width=3840, source_height=2160, aspect=16 / 9, min_window=0.5)
# One frame width (or height, or the whole zoom range) per second at full deflection.
UNIT_SPEEDS = CropSpeeds(pan=1.0, tilt=1.0, zoom=1.0)


def test_the_widest_window_is_the_whole_picture_on_a_source_of_the_same_shape() -> None:
    whole = FOUR_K.whole()

    assert whole == CropWindow(0.5, 0.5, 1.0)
    assert FOUR_K.pixels(whole) == PixelCrop(0, 0, 0, 0)
    assert FOUR_K.zoom_of(whole) == 0.0
    assert FOUR_K.magnification(whole) == 1.0


def test_the_window_shape_decides_whether_width_or_height_limits_the_widest_window() -> None:
    # A 4:3 source (1440x1080) is taller than 16:9: a window as wide as the
    # picture is 810 rows tall, which fits, so the widest window is the full
    # width and 0.75 of the height.
    four_by_three = CropFrame(1440, 1080, 16 / 9, 0.5)
    assert four_by_three.widest == 1.0
    assert four_by_three.height_of(1.0) == pytest.approx(0.75)

    # An ultrawide source (2560x1080) is wider than 16:9: a full-height window
    # is only 1920 px wide, so the widest window is 0.75 of the width.
    ultrawide = CropFrame(2560, 1080, 16 / 9, 0.5)
    assert ultrawide.widest == pytest.approx(0.75)
    assert ultrawide.height_of(0.75) == pytest.approx(1.0)


def test_zooming_in_shrinks_the_window_around_its_centre_and_keeps_16_by_9() -> None:
    # Full zoom-in speed for half a second: half the zoom range, from wide.
    window = FOUR_K.drive(FOUR_K.whole(), Velocity(zoom=1.0), 0.5, UNIT_SPEEDS)

    assert window.centre_x == pytest.approx(0.5)
    assert window.centre_y == pytest.approx(0.5)
    assert window.width == pytest.approx(0.75)  # halfway between 1.0 and 0.5
    crop = FOUR_K.pixels(window)
    width_px, height_px = 3840 - crop.left - crop.right, 2160 - crop.top - crop.bottom
    assert (width_px, height_px) == (2880, 1620)
    assert width_px / height_px == pytest.approx(16 / 9)


def test_zoom_stops_at_min_window_and_never_goes_narrower() -> None:
    # Zooming in for ten seconds would go far past the end of the range.
    window = FOUR_K.drive(FOUR_K.whole(), Velocity(zoom=1.0), 10.0, UNIT_SPEEDS)

    assert window.width == pytest.approx(0.5)
    assert FOUR_K.zoom_of(window) == 1.0
    assert FOUR_K.magnification(window) == pytest.approx(2.0)
    crop = FOUR_K.pixels(window)
    assert 3840 - crop.left - crop.right == 1920  # 1080p, no upscaling


def test_panning_moves_the_centre_and_stops_at_the_edge_of_the_picture() -> None:
    zoomed = CropWindow(0.5, 0.5, 0.5)

    # A short pan right moves the centre by speed x time.
    moved = FOUR_K.drive(zoomed, Velocity(pan=0.5), 0.2, UNIT_SPEEDS)
    assert moved.centre_x == pytest.approx(0.6)

    # A long one stops with the window's right side on the picture's right side.
    far = FOUR_K.drive(zoomed, Velocity(pan=1.0), 5.0, UNIT_SPEEDS)
    assert far.centre_x == pytest.approx(0.75)
    assert FOUR_K.pixels(far).right == 0


def test_a_positive_tilt_looks_up_so_the_centre_moves_towards_the_top() -> None:
    zoomed = CropWindow(0.5, 0.5, 0.5)

    moved = FOUR_K.drive(zoomed, Velocity(tilt=0.5), 0.2, UNIT_SPEEDS)

    assert moved.centre_y == pytest.approx(0.4)  # y counts from the top


def test_zooming_out_near_an_edge_pushes_the_window_back_inside() -> None:
    # A narrow window in the top-left corner, then zoom all the way out.
    corner = FOUR_K.clamp(CropWindow(0.0, 0.0, 0.5))
    assert corner.centre_x == pytest.approx(0.25)
    assert corner.centre_y == pytest.approx(0.25)

    wide = FOUR_K.drive(corner, Velocity(zoom=-1.0), 5.0, UNIT_SPEEDS)

    assert wide == CropWindow(0.5, 0.5, 1.0)


def test_pixels_and_back_give_the_same_window() -> None:
    window = CropWindow(0.3, 0.6, 0.6)

    crop = FOUR_K.pixels(window)
    back = FOUR_K.window_of(crop)

    assert back.centre_x == pytest.approx(0.3, abs=1e-3)
    assert back.centre_y == pytest.approx(0.6, abs=1e-3)
    assert back.width == pytest.approx(0.6, abs=1e-3)
    assert crop.filter_settings() == {
        "relative": True,
        "left": crop.left,
        "top": crop.top,
        "right": crop.right,
        "bottom": crop.bottom,
    }


def test_a_crop_made_by_hand_in_obs_is_adopted_as_the_nearest_allowed_window() -> None:
    # Someone cropped a tiny 400 px window in OBS: narrower than min_window.
    adopted = FOUR_K.window_of(PixelCrop(left=100, top=100, right=3340, bottom=1800))

    assert adopted.width == pytest.approx(0.5)
    assert FOUR_K.rect(adopted)["x"] == pytest.approx(0.0)  # pushed inside the picture


def test_centring_on_a_point_keeps_the_size_and_stays_inside() -> None:
    zoomed = CropWindow(0.5, 0.5, 0.5)

    assert FOUR_K.centred_on(zoomed, 0.4, 0.3) == CropWindow(0.4, 0.3, 0.5)
    # A click near the corner centres as near as the window allows.
    assert FOUR_K.centred_on(zoomed, 0.99, 0.01) == CropWindow(0.75, 0.25, 0.5)


def test_rect_gives_the_window_as_top_left_and_size() -> None:
    rect = FOUR_K.rect(CropWindow(0.5, 0.5, 0.5))

    assert rect == {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


def test_window_targets_describe_themselves_for_the_command_tail() -> None:
    assert WindowTarget(0.25, 0.4).describe() == "centre_window 0.25,0.40"
    assert WindowTarget(whole=True).describe() == "reset_crop"
