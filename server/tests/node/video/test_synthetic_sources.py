"""Tests for the synthetic cameras (node/video/synthetic_sources.py).

They are ffmpeg-generated pictures used instead of a webcam where there is
none (Linux CI, a laptop without its cameras plugged in).
"""

import pytest

from baja_studio.node.video.synthetic_sources import (
    QUADRANT_LEVELS,
    is_synthetic,
    synthetic_graph,
    synthetic_input_arguments,
)


def test_a_synthetic_device_starts_with_lavfi() -> None:
    assert is_synthetic("lavfi:testsrc2")
    assert not is_synthetic("EMEET SmartCam S600")


def test_the_test_card_has_the_requested_size_and_rate() -> None:
    assert synthetic_graph("testsrc2", 3840, 2160, 30) == "testsrc2=size=3840x2160:rate=30"


def test_the_quadrants_are_four_flat_greys_one_per_quarter() -> None:
    graph = synthetic_graph("quadrants", 3840, 2160, 30)
    # A black background, then three filled boxes of 1920x1080 for the other quarters.
    assert graph.startswith("color=c=0x000000:size=3840x2160:rate=30")
    assert "drawbox=x=1920:y=0:w=1920:h=1080:color=0x555555:t=fill" in graph  # top right
    assert "drawbox=x=0:y=1080:w=1920:h=1080:color=0xaaaaaa:t=fill" in graph  # bottom left
    assert "drawbox=x=1920:y=1080:w=1920:h=1080:color=0xffffff:t=fill" in graph  # bottom right
    # The levels a test expects to read back, darkest at top left.
    assert QUADRANT_LEVELS == {
        "top_left": 0,
        "top_right": 85,
        "bottom_left": 170,
        "bottom_right": 255,
    }


def test_an_unknown_synthetic_camera_is_refused_with_the_known_names() -> None:
    with pytest.raises(ValueError, match="known: testsrc2, quadrants"):
        synthetic_graph("smpte", 1920, 1080, 30)


def test_synthetic_input_is_read_in_real_time() -> None:
    assert synthetic_input_arguments("lavfi:testsrc2", 1280, 720, 25) == [
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=25",
    ]
