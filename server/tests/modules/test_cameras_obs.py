"""OBS crop cameras and previews inside the service (WP6b), end to end.

The `crop_studio` fixture (crop_studio.py) runs the cameras and playout
modules with a fake OBS, wired as in `main.py`: the cameras module reaches
OBS only through playout's connection. Commands are sent the way the
Cameras page sends them, previews are fetched over HTTP the way the page's
`CameraPreview` fetches them, and the checks read the Crop/Pad filter
inside the fake OBS, the module's state and the command log.
"""

import pytest

from baja_studio.core.module import CommandError
from baja_studio.core.supervisor import HealthState
from tests.modules.crop_studio import CROP_FILTER, WEBCAM, CropStudio

CENTRE_1080P = {"left": 960, "right": 960, "top": 540, "bottom": 540}


def running(studio: CropStudio, **filter_settings: int) -> CropStudio:
    """OBS connected, the webcam's filter in place, and the crop camera `ok`."""
    studio.obs.filters[WEBCAM] = []
    studio.connect_obs()
    studio.playout.obs_client.create_source_filter(
        WEBCAM, CROP_FILTER, "crop_filter", filter_settings or CENTRE_1080P
    )
    studio.add_crop_camera()
    studio.soon(lambda: studio.camera().get("status") == "ok")
    return studio


def test_a_crop_camera_comes_up_through_playouts_obs_connection(crop_studio: CropStudio) -> None:
    running(crop_studio)

    camera = crop_studio.camera()
    assert camera["driver"] == "obs_crop"
    assert camera["capabilities"]["kind"] == "crop"
    assert camera["crop"]["magnification"] == 2.0
    health = crop_studio.services.supervisor.module_health("cameras")
    assert health.state == HealthState.OK
    assert "crop ok" in health.message


def test_without_obs_the_crop_camera_is_offline_with_playouts_reason(
    crop_studio: CropStudio,
) -> None:
    # Playout has no OBS address yet: it never connects, and says so.
    crop_studio.add_crop_camera()

    crop_studio.soon(lambda: crop_studio.camera().get("status") == "offline")
    assert crop_studio.camera()["status_reason"].startswith("OBS offline: OBS not configured")


def test_the_web_page_moves_the_crop_through_the_arbiter(crop_studio: CropStudio) -> None:
    running(crop_studio)

    # Alice holds "right" (a move repeated every 100 ms, as the page does).
    for _ in range(5):
        crop_studio.command("move", pan=1.0, ttl_ms=300)
        crop_studio.pass_time(0.1)
    # Bob cannot take the camera while Alice moves it.
    with pytest.raises(CommandError, match="being moved by web:alice"):
        crop_studio.command("move", source="web:bob", pan=-1.0)
    crop_studio.command("stop", release=True)

    crop_studio.soon(lambda: crop_studio.camera()["moving"] is False)
    assert crop_studio.crop_settings()["left"] > 960 + 300  # it moved right
    assert crop_studio.crop_settings()["left"] + crop_studio.crop_settings()["right"] == 1920


def test_click_to_centre_and_reset_go_through_the_arbiter_too(crop_studio: CropStudio) -> None:
    running(crop_studio)

    crop_studio.command("centre_window", x=0.25, y=0.25)
    crop_studio.soon(lambda: crop_studio.crop_settings()["left"] == 0)
    assert crop_studio.camera()["control"]["last_command"] == "centre_window 0.25,0.25"

    crop_studio.command("reset_crop")
    crop_studio.soon(lambda: crop_studio.crop_settings()["right"] == 0)
    assert crop_studio.camera()["crop"]["magnification"] == 1.0

    with pytest.raises(CommandError, match="x must be a fraction of the picture"):
        crop_studio.command("centre_window", x=1.5, y=0.5)


def test_a_stored_preset_is_kept_in_the_cameras_settings_and_recalled(
    crop_studio: CropStudio,
) -> None:
    running(crop_studio)

    crop_studio.command("store_preset", n=2)
    crop_studio.soon(lambda: bool(crop_studio.saved_camera().crop.windows))
    stored = crop_studio.saved_camera().crop.windows
    assert [(w.n, w.centre_x, w.centre_y, w.width) for w in stored] == [(2, 0.5, 0.5, 0.5)]

    crop_studio.command("centre_window", x=0.25, y=0.25)
    crop_studio.soon(lambda: crop_studio.crop_settings()["left"] == 0)
    crop_studio.command("recall_preset", n=2)

    crop_studio.soon(lambda: crop_studio.crop_settings()["left"] == 960)


def test_create_filter_makes_a_degraded_camera_ok_and_refuses_a_second_one(
    crop_studio: CropStudio,
) -> None:
    crop_studio.connect_obs()
    crop_studio.add_crop_camera()
    crop_studio.soon(lambda: crop_studio.camera().get("status") == "degraded")

    assert crop_studio.command("create_crop_filter", camera="crop") == {
        "created": CROP_FILTER,
        "source": WEBCAM,
    }
    crop_studio.soon(lambda: crop_studio.camera().get("status") == "ok")

    with pytest.raises(CommandError, match="already has a filter called 'Baja crop'"):
        crop_studio.command("create_crop_filter", camera="crop")


def test_the_obs_input_list_is_offered_and_not_logged(crop_studio: CropStudio) -> None:
    # Before OBS is connected: an empty list and the reason.
    offline = crop_studio.command("list_obs_sources")
    assert offline["connected"] is False

    crop_studio.connect_obs()
    listed = crop_studio.command("list_obs_sources")

    assert listed["connected"] is True
    assert {"name": WEBCAM, "kind": "dshow_input"} in listed["sources"]
    logged = [entry["command"] for entry in crop_studio.services.command_log.tail("cameras", 50)]
    assert "list_obs_sources" not in logged


# --- previews ---------------------------------------------------------------------------------


def test_a_preview_is_a_still_of_the_window_with_where_the_window_was(
    crop_studio: CropStudio,
) -> None:
    running(crop_studio)

    answer = crop_studio.http.get("/api/cameras/preview/crop?width=320")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/png"  # the fake OBS answers in PNG
    assert answer.headers["x-preview-shows"] == "window"
    assert answer.headers["x-crop-window"] == "0.250000,0.250000,0.500000,0.500000"
    assert answer.headers["cache-control"] == "no-store"


def test_nobody_looking_means_no_screenshots_and_many_pages_share_one(
    crop_studio: CropStudio,
) -> None:
    running(crop_studio)

    # The camera runs for a while with no page showing previews: OBS is not asked.
    crop_studio.pass_time(2.0)
    assert crop_studio.screenshots(width=320) == 0

    # Three pages ask in the same second: one screenshot. A second later, another.
    for _ in range(3):
        assert crop_studio.http.get("/api/cameras/preview/crop").status_code == 200
    assert crop_studio.screenshots(width=320) == 1
    crop_studio.pass_time(1.0)
    crop_studio.http.get("/api/cameras/preview/crop")
    assert crop_studio.screenshots(width=320) == 2


def test_obs_going_away_turns_the_camera_offline_and_the_preview_explains(
    crop_studio: CropStudio,
) -> None:
    running(crop_studio)

    crop_studio.network.unplug("obs.test", 4455)
    crop_studio.soon(lambda: crop_studio.camera().get("status") == "offline")
    answer = crop_studio.http.get("/api/cameras/preview/crop")
    assert answer.status_code == 503
    assert answer.json()["detail"].startswith("OBS offline")

    # OBS is started again: playout reconnects, and the camera comes back.
    crop_studio.network.plug("obs.test", 4455, crop_studio.obs)
    crop_studio.soon(lambda: crop_studio.camera().get("status") == "ok", within_seconds=10.0)
    assert crop_studio.http.get("/api/cameras/preview/crop").status_code == 200


def test_a_camera_without_an_obs_source_has_no_preview(crop_studio: CropStudio) -> None:
    crop_studio.services.settings.update(
        "cameras", {"cameras": [{"id": "ptz", "address": {"host": "10.0.0.9"}}]}
    )

    answer = crop_studio.http.get("/api/cameras/preview/ptz")

    assert answer.status_code == 404
    assert "no OBS source" in answer.json()["detail"]
