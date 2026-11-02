"""Tests for a simulated crop camera's window (fakes/simulated_crop.py).

The window behaves like a PTZ head on a fixed frame: pan moves it right, a
positive tilt moves it up, zoom shrinks it around its centre, and it never
leaves the frame.
"""

import pytest

from baja_studio.agents.contract import CropRect
from baja_studio.fakes.simulated_crop import CROP_LIMITS, MIN_WINDOW_SIZE, CropWindow


def middle_window() -> CropWindow:
    """A 0.4 x 0.4 window in the middle of the frame."""
    return CropWindow.from_rect(CropRect(x=0.3, y=0.3, w=0.4, h=0.4))


def test_panning_right_moves_the_centre_right_at_the_top_speed() -> None:
    window = middle_window()

    window.drive((1.0, 0.0, 0.0), seconds=0.2)

    assert window.position().pan == pytest.approx(0.5 + CROP_LIMITS.pan_speed_max * 0.2)
    assert window.position().tilt == pytest.approx(0.5)


def test_a_positive_tilt_moves_the_window_up_the_frame() -> None:
    window = middle_window()

    window.drive((0.0, 1.0, 0.0), seconds=0.1)

    # Frame y grows downwards, so "up" is a smaller y.
    assert window.rect().y < 0.3


def test_zooming_in_shrinks_the_window_around_its_centre() -> None:
    window = middle_window()
    zoom_before = window.zoom()

    window.drive((0.0, 0.0, 1.0), seconds=0.2)

    rect = window.rect()
    assert window.zoom() > zoom_before
    assert rect.w < 0.4
    assert (rect.x + rect.w / 2, rect.y + rect.h / 2) == pytest.approx((0.5, 0.5))


def test_the_window_stops_at_the_edge_of_the_frame() -> None:
    window = middle_window()

    window.drive((1.0, 0.0, 0.0), seconds=10)

    rect = window.rect()
    assert rect.x + rect.w == pytest.approx(1.0)


def test_zooming_out_at_an_edge_keeps_the_window_inside_the_frame() -> None:
    window = CropWindow.from_rect(CropRect(x=0.8, y=0.0, w=0.2, h=0.2))

    window.drive((0.0, 0.0, -1.0), seconds=10)

    assert window.rect() == CropRect(x=0.0, y=0.0, w=1.0, h=1.0)
    assert window.zoom() == 0.0


def test_the_window_keeps_its_shape_while_zooming() -> None:
    window = CropWindow.from_rect(CropRect(x=0.0, y=0.25, w=1.0, h=0.5))

    window.drive((0.0, 0.0, 1.0), seconds=0.5)

    rect = window.rect()
    assert rect.w / rect.h == pytest.approx(2.0)


def test_a_window_narrower_than_the_minimum_is_widened_to_it() -> None:
    window = CropWindow.from_rect(CropRect(x=0.5, y=0.5, w=0.01, h=0.01))

    assert window.rect().w == pytest.approx(MIN_WINDOW_SIZE)
    assert window.zoom() == 1.0
