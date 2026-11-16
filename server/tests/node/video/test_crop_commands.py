"""Tests for turning a window into crop pixels and live crop commands (node/video/crop_commands.py).

Background for a beginner: the camera sends a big 3840x2160 ("4K") picture.
The node shows only a part of it, the *window*, enlarged to 1920x1080. A
window is given as fractions of the picture (x, y, width, height, each
0..1). ffmpeg's `crop` filter wants pixels, so these functions convert, and
build the text commands ffmpeg receives when the window moves.
"""

from baja_studio.node.video.crop_commands import (
    CROP_FILTER_TARGET,
    MIN_CROP_WIDTH_PIXELS,
    CropPixels,
    crop_filter,
    crop_pixels,
    widest_crop,
    window_commands,
)
from baja_studio.node.video.spec import SourceWindow

# A 4K camera and a 16:9 output, as on the S600 webcams.
WIDTH, HEIGHT, ASPECT = 3840, 2160, 16 / 9


def pixels(window: SourceWindow) -> CropPixels:
    """Shorthand: the pixels of `window` on the 4K camera."""
    return crop_pixels(window, WIDTH, HEIGHT, ASPECT)


def test_the_whole_frame_is_the_full_width_centred() -> None:
    # The whole 4K frame: 3840 pixels wide, centred at (1920, 1080).
    assert pixels(SourceWindow.whole()) == CropPixels(width=3840, centre_x=1920, centre_y=1080)


def test_a_quarter_window_is_half_the_width_centred_in_its_quarter() -> None:
    # The top-left quarter: x=0, y=0, half as wide and half as tall.
    # In pixels that is 1920 wide (2x zoom), centred at (960, 540).
    assert pixels(SourceWindow(0.0, 0.0, 0.5, 0.5)) == CropPixels(1920, 960, 540)
    # And the bottom-right quarter.
    assert pixels(SourceWindow(0.5, 0.5, 0.5, 0.5)) == CropPixels(1920, 2880, 1620)


def test_widths_are_rounded_to_an_even_number_of_pixels() -> None:
    # 0.3 x 3840 = 1152 (even already); 0.30013 x 3840 = 1152.5 -> rounds to 1152 or 1153,
    # and an odd result is brought down to even, because video stores colour at half
    # resolution and ffmpeg would round an odd width anyway.
    for fraction in (0.3, 0.30013, 0.4567, 0.71):
        assert pixels(SourceWindow(0.1, 0.1, fraction, fraction)).width % 2 == 0


def test_a_window_too_wide_is_brought_back_to_the_widest_possible() -> None:
    # Asking for 150 % of the width cannot be done: the result is the full width.
    assert pixels(SourceWindow(0.0, 0.0, 1.5, 1.5)).width == 3840


def test_a_window_too_narrow_is_kept_at_the_minimum_width() -> None:
    # A width of 0 would make ffmpeg refuse the command; the minimum is used instead.
    assert pixels(SourceWindow(0.5, 0.5, 0.0, 0.0)).width == MIN_CROP_WIDTH_PIXELS


def test_a_window_hanging_off_an_edge_is_moved_back_inside() -> None:
    # A 2x window whose top-left corner is at (0.9, 0.9) would stick out of the
    # frame. Its centre is moved so the whole window fits: 1920 wide means a centre
    # no further right than 3840 - 960 = 2880, and 1080 tall no lower than 1620.
    assert pixels(SourceWindow(0.9, 0.9, 0.5, 0.5)) == CropPixels(1920, 2880, 1620)
    # The same on the other side: a negative corner is pulled back to the edge.
    assert pixels(SourceWindow(-0.4, -0.4, 0.5, 0.5)) == CropPixels(1920, 960, 540)


def test_the_window_height_only_places_the_centre() -> None:
    # The output is always 16:9, so a height that does not match the width
    # (here 0.9 instead of 0.5) cannot stretch the picture. It only moves the
    # centre: y + h/2 = 0.05 + 0.45 = 0.5 of the height, i.e. 1080.
    assert pixels(SourceWindow(0.25, 0.05, 0.5, 0.9)) == CropPixels(1920, 1920, 1080)


def test_the_widest_window_of_a_taller_source_is_limited_by_its_height() -> None:
    # A 4:3 camera (1440x1080): a 16:9 window can be at most 1080 x 16/9 = 1920
    # wide... which is more than 1440, so the full 1440 width is the limit.
    assert widest_crop(1440, 1080, ASPECT) == 1440
    # A very wide source (3840x1000): the height limits it to 1000 x 16/9 = 1777 -> 1776 (even).
    assert widest_crop(3840, 1000, ASPECT) == 1776


def test_the_crop_filter_keeps_the_output_shape_and_follows_the_centre() -> None:
    # The filter as written on ffmpeg's command line. Its height is a formula of
    # its width (`ow` = output width), and its left/top edges are formulas of the
    # centre, so changing only the width later keeps a correct 16:9 window around
    # the same centre.
    text = crop_filter(CropPixels(1920, 960, 540), 1920, 1080)
    assert text == f"{CROP_FILTER_TARGET}=w=1920:h=ow*1080/1920:x=960-ow/2:y=540-oh/2"


def test_with_no_previous_window_all_three_commands_are_sent() -> None:
    # The first time, we do not know what ffmpeg shows: send the width, then x, then y.
    assert window_commands(None, CropPixels(1920, 960, 540)) == [
        "crop@window w 1920",
        "crop@window x 960-ow/2",
        "crop@window y 540-oh/2",
    ]


def test_a_pan_sends_only_x() -> None:
    # Moving sideways changes only the centre's x: one command, not three.
    before = CropPixels(1920, 960, 540)
    after = CropPixels(1920, 1000, 540)
    assert window_commands(before, after) == ["crop@window x 1000-ow/2"]


def test_a_zoom_sends_only_the_width() -> None:
    # Zooming around the same centre: x and y are formulas of the width, so they follow.
    before = CropPixels(3840, 1920, 1080)
    after = CropPixels(2880, 1920, 1080)
    assert window_commands(before, after) == ["crop@window w 2880"]


def test_a_diagonal_move_sends_x_and_y_and_nothing_for_the_same_window() -> None:
    before = CropPixels(1920, 960, 540)
    assert window_commands(before, CropPixels(1920, 980, 560)) == [
        "crop@window x 980-ow/2",
        "crop@window y 560-oh/2",
    ]
    # The very same window again: nothing to send at all.
    assert window_commands(before, before) == []


def test_a_larger_minimum_width_can_be_asked_for() -> None:
    # The Rockchip RGA scales 8x at most, so the NanoPi asks for 240 pixels at least.
    narrow = crop_pixels(SourceWindow(0.5, 0.5, 0.0, 0.0), WIDTH, HEIGHT, ASPECT, min_width=240)
    assert narrow.width == 240
