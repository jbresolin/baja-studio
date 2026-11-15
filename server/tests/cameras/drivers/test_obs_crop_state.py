"""Tests for what an OBS crop camera publishes (cameras/drivers/obs_crop_state.py).

The camera's `crop` entry is what the preview draws its window from, and
`position` is the contract's crop position. While the window moves, these
change 25 times a second; the state goes to every open page, so they are
published at most ten times a second then, and at once when it stops.
"""

from typing import Any

from baja_studio.agents.contract import Status
from baja_studio.cameras.crop_window import CropFrame, CropWindow
from baja_studio.cameras.drivers.obs_crop_state import CropPublisher, crop_state
from tests.cameras.drivers.obs_bench import crop_camera

FOUR_K = CropFrame(3840, 2160, 16 / 9, 0.5)


def test_the_crop_entry_has_the_window_its_zoom_and_the_source_size() -> None:
    entry = crop_state(FOUR_K, CropWindow(0.5, 0.5, 0.625), crop_camera())

    assert entry == {
        "x": 0.1875,
        "y": 0.1875,
        "w": 0.625,
        "h": 0.625,
        "centre_x": 0.5,
        "centre_y": 0.5,
        "zoom": 0.75,
        "magnification": 1.6,  # the page shows "1.6x"
        "source_width": 3840,
        "source_height": 2160,
        "filter": "Baja crop",
    }


def publish(publisher: CropPublisher, now: float, centre_x: float, moving: bool) -> None:
    crop = crop_state(FOUR_K, CropWindow(centre_x, 0.5, 0.5), crop_camera())
    publisher.publish(now, moving=moving, crop=crop, status=Status.OK, reason="", last_error="")


def test_a_moving_window_is_published_ten_times_a_second_and_at_once_when_it_stops() -> None:
    published: list[tuple[str, Any]] = []
    publisher = CropPublisher(lambda key, value: published.append((key, value)))

    # 25 steps a second for 0.4 s: the window moves a little every 40 ms.
    for step in range(10):
        publish(publisher, step * 0.04, 0.3 + step * 0.01, moving=True)
    crops_while_moving = [value["centre_x"] for key, value in published if key == "crop"]
    # Then it stops, 0.04 s after the last step: published at once.
    publish(publisher, 0.40, 0.4, moving=False)

    assert crops_while_moving == [0.3, 0.33, 0.36, 0.39]  # every 0.1 s (every third step)
    last_crop = [value for key, value in published if key == "crop"][-1]
    last_moving = [value for key, value in published if key == "moving"][-1]
    assert last_crop["centre_x"] == 0.4  # the resting window, published at once
    assert last_moving is False


def test_status_is_published_last_and_only_when_it_changed() -> None:
    published: list[str] = []
    publisher = CropPublisher(lambda key, value: published.append(key))

    publish(publisher, 0.0, 0.5, moving=False)
    first = list(published)
    publish(publisher, 1.0, 0.5, moving=False)  # nothing changed

    assert first[-1] == "status"
    assert published == first
    assert publisher.position is not None and publisher.position.pan == 0.5
