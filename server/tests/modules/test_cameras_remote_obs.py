"""Crop cameras whose 4K webcam is in a trackside laptop's OBS (WP13, ADR 0021), end to end.

At the race a laptop beside the track runs its own OBS with the 4K webcam
and its "Baja crop" filter; the studio must move that filter exactly as it
moves one in the streaming PC's own OBS. These tests run the whole service
(`crop_studio`, see crop_studio.py) with two fake OBSes on the in-process
network: the streaming PC's, which playout connects to, and a laptop's at
laptop.test:4455 with two 4K webcams ("Laptop cam A" and "Laptop cam B").

What we look at: the camera's state (status and reason), the Crop/Pad
filter inside the laptop's fake OBS (that is what goes on air), and how many
connections the laptop's OBS has open (`session_count`).
"""

from typing import Any

from baja_studio.fakes.fake_obs import FakeObs
from tests.modules.crop_studio import (
    CROP_FILTER,
    LAPTOP_CAM_A,
    LAPTOP_CAM_B,
    LAPTOP_HOST,
    LAPTOP_PASSWORD,
    LAPTOP_PORT,
    CropStudio,
)

LAPTOP = f"{LAPTOP_HOST}:{LAPTOP_PORT}"


def remote_camera(
    camera_id: str, source: str, password: str = LAPTOP_PASSWORD, host: str = LAPTOP_HOST
) -> dict[str, Any]:
    """An `obs_crop` camera whose source is in the OBS at host:4455, as Camera setup saves it."""
    obs = {"remote": True, "host": host, "port": LAPTOP_PORT, "password": password}
    return {"id": camera_id, "driver": "obs_crop", "obs_source": source, "crop": {"obs": obs}}


def status_of(studio: CropStudio, camera_id: str) -> str:
    return str(studio.camera(camera_id).get("status"))


def all_ok(studio: CropStudio, *camera_ids: str) -> bool:
    return all(status_of(studio, camera_id) == "ok" for camera_id in camera_ids)


def zoom_in(studio: CropStudio, camera_id: str, seconds: float = 0.5) -> None:
    """Hold "zoom in" on one camera from the web page (a move every 100 ms), then let go."""
    for _ in range(round(seconds / 0.1)):
        studio.command("move", camera=camera_id, zoom=1.0, ttl_ms=300)
        studio.pass_time(0.1)
    studio.command("stop", camera=camera_id, release=True)
    studio.soon(lambda: studio.camera(camera_id)["moving"] is False)
    assert studio.camera(camera_id)["crop"]["magnification"] > 1.0, "it did not zoom"


def writes_to(obs: FakeObs, source: str) -> int:
    """How many times the studio moved the crop filter of `source` in that OBS."""
    return sum(
        1
        for record in obs.requests_of("SetSourceFilterSettings")
        if record.data.get("sourceName") == source
    )


# --- coming up --------------------------------------------------------------------------------


def test_a_remote_crop_camera_comes_up_through_its_own_connection(crop_studio: CropStudio) -> None:
    # Playout has no OBS at all here: a laptop's OBS does not need it.
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))

    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok")

    camera = crop_studio.camera("lap-a")
    # The filter on the laptop is empty: the whole 4K picture, no zoom yet.
    assert camera["crop"]["source_width"] == 3840
    assert camera["crop"]["magnification"] == 1.0
    # The pages can show which OBS this camera uses.
    assert camera["obs"] == {"remote": True, "address": LAPTOP, "note": ""}
    # Connecting never moves the picture: nothing was written to the laptop's filter.
    assert writes_to(crop_studio.laptop, LAPTOP_CAM_A) == 0
    # And the streaming PC's OBS was never asked anything about the laptop's webcam.
    assert not [r for r in crop_studio.obs.requests if LAPTOP_CAM_A in str(r.data)]


def test_two_cameras_on_one_laptop_share_one_connection_and_move_independently(
    crop_studio: CropStudio,
) -> None:
    # One laptop with two 4K webcams: two crop cameras, one OBS.
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B)
    )
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"))

    # One connection to the laptop, not one per camera.
    assert len(crop_studio.remote_connections()) == 1
    assert crop_studio.laptop.session_count == 1

    # Zooming camera A changes only webcam A's filter; B's is never written.
    zoom_in(crop_studio, "lap-a")
    assert crop_studio.laptop_crop(LAPTOP_CAM_A)["left"] > 0
    assert writes_to(crop_studio.laptop, LAPTOP_CAM_B) == 0
    assert crop_studio.camera("lap-b")["crop"]["magnification"] == 1.0


# --- connections opened and closed with the settings --------------------------------------


def test_a_new_address_moves_the_camera_to_another_connection_and_closes_the_old_one(
    crop_studio: CropStudio,
) -> None:
    # A second laptop, with a webcam of the same name.
    other = FakeObs(crop_studio.clock, password=LAPTOP_PASSWORD)
    other.add_video_input(LAPTOP_CAM_A, 3840, 2160)
    other.add_crop_filter(LAPTOP_CAM_A, CROP_FILTER)
    crop_studio.network.plug("laptop2.test", LAPTOP_PORT, other)
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok")

    # The operator types the second laptop's address on Camera setup and saves.
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A, host="laptop2.test"))
    crop_studio.soon(lambda: other.session_count == 1 and status_of(crop_studio, "lap-a") == "ok")

    # The first laptop is no longer used by anyone, so its connection was closed.
    assert crop_studio.laptop.session_count == 0
    workers = crop_studio.services.supervisor.worker_names("cameras")
    assert "remote-obs:laptop2.test:4455" in workers
    assert "remote-obs:laptop.test:4455" not in workers
    assert crop_studio.camera("lap-a")["obs"]["address"] == "laptop2.test:4455"


def test_the_connection_closes_only_when_the_last_camera_using_it_goes(
    crop_studio: CropStudio,
) -> None:
    both = (remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B))
    crop_studio.save_cameras(*both)
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"))

    # Camera A is removed: B still uses the laptop, so the connection stays.
    crop_studio.save_cameras(both[1])
    crop_studio.pass_time(0.5)
    assert crop_studio.laptop.session_count == 1
    assert status_of(crop_studio, "lap-b") == "ok"

    # Camera B is switched off on Camera setup: nobody uses the laptop any more.
    crop_studio.save_cameras({**both[1], "enabled": False})
    crop_studio.soon(lambda: crop_studio.laptop.session_count == 0)
    assert crop_studio.remote_connections() == []


def test_a_camera_moved_back_to_this_pcs_obs_closes_the_laptop_connection(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok")

    # "OBS: this PC" again (the address and password are kept, but not used).
    camera = remote_camera("lap-a", LAPTOP_CAM_A)
    camera["crop"]["obs"]["remote"] = False
    crop_studio.save_cameras(camera)

    crop_studio.soon(lambda: crop_studio.laptop.session_count == 0)
    # Playout has no OBS configured, so the camera now shows playout's reason.
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "offline")
    reason = crop_studio.camera("lap-a")["status_reason"]
    assert reason.startswith("OBS offline: OBS not configured")
    assert crop_studio.camera("lap-a")["obs"] == {"remote": False, "address": None, "note": ""}


# --- why a laptop's cameras are down ---------------------------------------------------------


def test_a_wrong_password_makes_both_cameras_degraded_with_the_same_words(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A, password="wrong"),
        remote_camera("lap-b", LAPTOP_CAM_B, password="wrong"),
    )

    # Degraded, not offline: the laptop's OBS is there, our settings are wrong.
    crop_studio.soon(
        lambda: (
            status_of(crop_studio, "lap-a") == "degraded"
            and status_of(crop_studio, "lap-b") == "degraded"
        )
    )
    expected = f"OBS at {LAPTOP} refused the password (check it on Camera setup)"
    assert crop_studio.camera("lap-a")["status_reason"] == expected
    assert crop_studio.camera("lap-b")["status_reason"] == expected

    # The right password is saved on both: no restart, the connection is redialled.
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B)
    )
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"), within_seconds=10.0)


def test_an_unreachable_laptop_makes_its_cameras_offline_naming_it(
    crop_studio: CropStudio,
) -> None:
    # Nothing answers at nowhere.test (a laptop switched off, a wrong address).
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A, host="nowhere.test"))

    crop_studio.soon(lambda: crop_studio.camera("lap-a").get("status_reason", "").startswith("OBS"))
    camera = crop_studio.camera("lap-a")
    assert camera["status"] == "offline"
    assert camera["status_reason"] == "OBS at nowhere.test:4455 unreachable: connection refused"


def test_two_passwords_for_one_laptop_the_camera_saved_last_wins_and_setup_says_so(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B)
    )
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"))

    # Someone edits camera B and types a wrong password. One connection can only
    # use one password: B's, saved last. So both cameras are refused now...
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A),
        remote_camera("lap-b", LAPTOP_CAM_B, password="typo"),
    )
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "degraded")
    # ...and camera A's entry says why its own (right) password is not used.
    note = crop_studio.camera("lap-a")["obs"]["note"]
    assert "camera 'lap-b' was saved later with another password" in note
    assert crop_studio.camera("lap-b")["obs"]["note"] == ""

    # B is corrected: both come back, and the note goes away.
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B)
    )
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok", within_seconds=10.0)
    assert crop_studio.camera("lap-a")["obs"]["note"] == ""


# --- the laptop going away and coming back ------------------------------------------------


def test_the_laptops_obs_going_away_and_back_keeps_the_window_and_moves_nothing(
    crop_studio: CropStudio,
) -> None:
    crop_studio.save_cameras(
        remote_camera("lap-a", LAPTOP_CAM_A), remote_camera("lap-b", LAPTOP_CAM_B)
    )
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"))
    zoom_in(crop_studio, "lap-a")
    window_before = crop_studio.laptop_crop(LAPTOP_CAM_A)
    writes_before = writes_to(crop_studio.laptop, LAPTOP_CAM_A)

    # The laptop's OBS is closed: both cameras go offline, naming the laptop.
    crop_studio.network.unplug(LAPTOP_HOST, LAPTOP_PORT)
    crop_studio.soon(
        lambda: (
            status_of(crop_studio, "lap-a") == "offline"
            and status_of(crop_studio, "lap-b") == "offline"
        )
    )
    assert crop_studio.camera("lap-a")["status_reason"].startswith(f"OBS at {LAPTOP} ")

    # OBS is opened again: the connection comes back by itself.
    crop_studio.network.plug(LAPTOP_HOST, LAPTOP_PORT, crop_studio.laptop)
    crop_studio.soon(lambda: all_ok(crop_studio, "lap-a", "lap-b"), within_seconds=10.0)

    # The crop OBS kept is taken as the window: same picture, nothing written.
    assert crop_studio.laptop_crop(LAPTOP_CAM_A) == window_before
    assert writes_to(crop_studio.laptop, LAPTOP_CAM_A) == writes_before
    assert crop_studio.camera("lap-a")["crop"]["magnification"] > 1.0


def test_a_pulled_network_cable_is_noticed_and_the_link_redialled_when_it_is_back(
    crop_studio: CropStudio,
) -> None:
    # A pulled cable closes nothing: requests just never get an answer. The
    # connection checks every 2 s that OBS still answers, and redials.
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok")
    window_before = crop_studio.laptop_crop(LAPTOP_CAM_A)

    crop_studio.network.hold_answers(LAPTOP_HOST, LAPTOP_PORT)
    for _ in range(80):  # up to 8 s of fake time, while the laptop's answers are held
        crop_studio.settle(wait_for_remote_answers=False)
        if status_of(crop_studio, "lap-a") == "offline":
            break
        crop_studio.clock.advance(0.1)
    assert status_of(crop_studio, "lap-a") == "offline"
    assert crop_studio.camera("lap-a")["status_reason"].startswith(f"OBS at {LAPTOP}")

    # The cable is plugged back in.
    crop_studio.network.release_answers(LAPTOP_HOST, LAPTOP_PORT)
    crop_studio.soon(lambda: status_of(crop_studio, "lap-a") == "ok", within_seconds=10.0)
    assert crop_studio.laptop_crop(LAPTOP_CAM_A) == window_before
    assert writes_to(crop_studio.laptop, LAPTOP_CAM_A) == 0
