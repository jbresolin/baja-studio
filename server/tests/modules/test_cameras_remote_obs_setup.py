"""Camera setup, previews and a slow laptop, for crop cameras in a remote OBS (WP13, ADR 0021).

Same set-up as test_cameras_remote_obs.py: the whole service with two fake
OBSes, the streaming PC's (reached through playout) and a trackside laptop's
at laptop.test:4455 with two 4K webcams. Here we press Camera setup's
buttons (Test connection, the source list, Create filter) the way the page
does, fetch previews over HTTP the way the page's `CameraPreview` does, and
check that a laptop that stops answering never slows down the streaming
PC's own crop camera.
"""

from typing import Any

from baja_studio.adapters.obs_images import image_size
from baja_studio.core.readiness import CheckStatus
from baja_studio.fakes.fake_obs import FakeObs
from tests.modules.crop_studio import (
    CROP_FILTER,
    LAPTOP_CAM_A,
    LAPTOP_CAM_B,
    LAPTOP_HOST,
    LAPTOP_PASSWORD,
    LAPTOP_PORT,
    WEBCAM,
    CropStudio,
)
from tests.modules.readiness_runner import assert_fix_given, run_module_checks
from tests.modules.test_cameras_remote_obs import all_ok, remote_camera, status_of

MASKED = "********"  # how the settings API shows a saved password; the page sends it back


def obs_form(password: str = LAPTOP_PASSWORD, host: str = LAPTOP_HOST) -> dict[str, Any]:
    """What Camera setup's OBS fields hold: "remote OBS" ticked, address, port, password."""
    return {"remote": True, "host": host, "port": LAPTOP_PORT, "password": password}


def logged_commands(studio: CropStudio) -> list[str]:
    return [entry["command"] for entry in studio.services.command_log.tail("cameras", 100)]


# --- Test connection -----------------------------------------------------------------------------


def test_test_connection_says_connected_with_obss_version(crop_studio: CropStudio) -> None:
    reply = crop_studio.command("test_obs_connection", obs=obs_form())

    assert reply["result"] == "connected"
    assert reply["message"] == (
        "connected to OBS 32.0.0-fake (obs-websocket 5.6.0) at laptop.test:4455"
    )
    # The test connection is closed again: it was only for the question.
    assert crop_studio.laptop.session_count == 0


def test_test_connection_tells_a_wrong_password_from_an_unreachable_laptop(
    crop_studio: CropStudio,
) -> None:
    wrong = crop_studio.command("test_obs_connection", obs=obs_form(password="nope"))
    nobody = crop_studio.command("test_obs_connection", obs=obs_form(host="nowhere.test"))

    assert (wrong["result"], wrong["message"]) == (
        "wrong_password",
        "OBS at laptop.test:4455 refused the password",
    )
    assert (nobody["result"], nobody["message"]) == (
        "unreachable",
        "OBS at nowhere.test:4455 unreachable: connection refused",
    )


def test_a_masked_password_means_the_one_saved_with_the_camera(crop_studio: CropStudio) -> None:
    # The camera is saved with the right password. When the form is opened again
    # the page shows (and sends back) the mask, never the password itself.
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))

    reply = crop_studio.command("test_obs_connection", camera="lap-a", obs=obs_form(MASKED))

    assert reply["result"] == "connected"


def test_test_connection_is_kept_out_of_the_command_log(crop_studio: CropStudio) -> None:
    # Its arguments hold a password: the command log (shown on pages) must not.
    crop_studio.command("test_obs_connection", obs=obs_form())
    crop_studio.command("list_obs_sources", obs=obs_form())

    assert "test_obs_connection" not in logged_commands(crop_studio)
    assert "list_obs_sources" not in logged_commands(crop_studio)


def test_bad_obs_settings_are_refused_without_repeating_them(crop_studio: CropStudio) -> None:
    # A remote OBS without an address; the password must not appear in the error.
    try:
        crop_studio.command("test_obs_connection", obs={"remote": True, "password": "hush"})
    except Exception as refused:
        assert "needs its address" in str(refused)
        assert "hush" not in str(refused)
    else:
        raise AssertionError("an OBS without an address was accepted")


# --- the source picker ---------------------------------------------------------------------------


def test_the_source_list_comes_from_the_laptop_before_any_camera_uses_it(
    crop_studio: CropStudio,
) -> None:
    # A new camera: nothing saved yet, so no connection is open to the laptop.
    listed = crop_studio.command("list_obs_sources", obs=obs_form())

    names = [source["name"] for source in listed["sources"]]
    assert listed["connected"] is True
    assert names == [LAPTOP_CAM_A, LAPTOP_CAM_B]  # the laptop's, not the streaming PC's
    assert crop_studio.laptop.session_count == 0  # the short connection was closed


def test_the_source_list_uses_the_open_connection_once_a_camera_uses_the_laptop(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a"))

    listed = crop_studio.command("list_obs_sources", camera="lap-a", obs=obs_form(MASKED))

    assert listed["connected"] is True
    assert crop_studio.laptop.session_count == 1  # no second connection was opened


def test_the_source_list_explains_a_wrong_password(crop_studio: CropStudio) -> None:
    listed = crop_studio.command("list_obs_sources", obs=obs_form(password="nope"))

    assert listed == {
        "connected": False,
        "reason": "OBS at laptop.test:4455 refused the password",
        "sources": [],
    }


def test_without_obs_settings_the_list_is_still_playouts_obs(crop_studio: CropStudio) -> None:
    crop_studio.connect_obs()

    listed = crop_studio.command("list_obs_sources")

    assert {"name": WEBCAM, "kind": "dshow_input"} in listed["sources"]


# --- Create filter -------------------------------------------------------------------------------


def test_create_filter_makes_the_filter_in_the_laptops_obs(crop_studio: CropStudio) -> None:
    # A third webcam on the laptop, without a Crop/Pad filter yet.
    crop_studio.laptop.add_video_input("Laptop cam C", 3840, 2160)
    crop_studio.save_cameras(remote_camera("lap-c", "Laptop cam C"))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-c") == "degraded")

    created = crop_studio.command("create_crop_filter", camera="lap-c")

    assert created == {"created": CROP_FILTER, "source": "Laptop cam C"}
    crop_studio.soon(lambda: status_of(crop_studio, "lap-c") == "ok")
    # Made on the laptop; the streaming PC's OBS was not touched.
    assert crop_studio.obs.requests_of("CreateSourceFilter") == []


# --- previews ------------------------------------------------------------------------------------


def test_a_remote_cameras_preview_is_a_still_from_the_laptops_obs(crop_studio: CropStudio) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a"))

    answer = crop_studio.http.get("/api/cameras/preview/lap-a?width=320")

    assert answer.status_code == 200
    assert answer.headers["x-preview-shows"] == "window"
    shots = crop_studio.laptop.requests_of("GetSourceScreenshot")
    assert [shot.data.get("imageWidth") for shot in shots][-1] == 320


def test_two_laptops_with_a_webcam_of_the_same_name_never_share_a_preview(
    crop_studio: CropStudio,
) -> None:
    # Both laptops call their webcam "Laptop cam A"; the second one's is 4:3.
    other = FakeObs(crop_studio.clock, password=LAPTOP_PASSWORD)
    other.add_video_input(LAPTOP_CAM_A, 1440, 1080)
    other.add_crop_filter(LAPTOP_CAM_A, CROP_FILTER)
    crop_studio.network.plug("laptop2.test", LAPTOP_PORT, other)
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A),
        remote_camera("lap2-a", LAPTOP_CAM_A, host="laptop2.test"),
    )
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap2-a"))

    first = crop_studio.http.get("/api/cameras/preview/lap-a?width=320")
    second = crop_studio.http.get("/api/cameras/preview/lap2-a?width=320")

    # Same source name, same width, same second: still two screenshots, one per OBS.
    assert image_size(first.content) == (320, 180)
    assert image_size(second.content) == (320, 240)


def test_an_unreachable_laptops_preview_says_which_obs(crop_studio: CropStudio) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A, host="nowhere.test"))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "offline")

    answer = crop_studio.http.get("/api/cameras/preview/lap-a")

    assert answer.status_code == 503
    assert answer.json()["detail"].startswith("OBS at nowhere.test:4455 unreachable")


# --- a slow laptop never slows the rest ----------------------------------------------------------


def test_a_laptop_that_stops_answering_never_delays_the_local_crop_camera(
    crop_studio: CropStudio,
) -> None:
    # The streaming PC's own crop camera, through playout's OBS...
    crop_studio.obs.filters[WEBCAM] = []
    crop_studio.connect_obs()
    crop_studio.playout.obs_client.create_source_filter(
        WEBCAM, CROP_FILTER, "crop_filter", {"left": 960, "right": 960, "top": 540, "bottom": 540}
    )
    local = {"id": "crop", "driver": "obs_crop", "obs_source": WEBCAM}
    # ...and a laptop's camera beside it.
    crop_studio.save_cameras(local, remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: all_ok(crop_studio, "crop", "lap-a"))

    # The radio link to the laptop stops carrying its answers. Its camera's
    # requests now wait (up to 3 s) for answers that do not come.
    crop_studio.network.hold_answers(LAPTOP_HOST, LAPTOP_PORT)
    crop_studio.command("move", camera="lap-a", zoom=1.0, ttl_ms=300)

    # Meanwhile the operator holds "right" on the local camera for half a second.
    for _ in range(5):
        crop_studio.command("move", camera="crop", pan=1.0, ttl_ms=300)
        crop_studio.settle(wait_for_remote_answers=False)
        crop_studio.clock.advance(0.1)
        crop_studio.settle(wait_for_remote_answers=False)
    crop_studio.command("stop", camera="crop", release=True)
    crop_studio.settle(wait_for_remote_answers=False)

    # The local camera moved as far as it would with no laptop at all
    # (test_cameras_obs.py: more than 150 px in half a second at full speed).
    assert crop_studio.crop_settings()["left"] > 960 + 150
    # The local preview still answers at once, while the laptop's waits.
    assert crop_studio.http.get("/api/cameras/preview/crop").status_code == 200

    # The link recovers: the laptop's camera comes back by itself.
    crop_studio.network.release_answers(LAPTOP_HOST, LAPTOP_PORT)
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a"), within_seconds=10.0)


# --- readiness -----------------------------------------------------------------------------------


def test_readiness_sends_the_operator_to_the_laptop_not_to_playout(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A, host="nowhere.test"))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "offline")

    result = run_module_checks(crop_studio.services, "cameras", crop_studio.clock)["camera:lap-a"]

    assert result.status == CheckStatus.FAIL
    assert "OBS at nowhere.test:4455 unreachable" in result.detail
    assert result.fix.startswith("Check the laptop at nowhere.test is on and on the network")
    assert "port 4455" in result.fix and "Playout setup" not in result.fix
    assert_fix_given(result)
