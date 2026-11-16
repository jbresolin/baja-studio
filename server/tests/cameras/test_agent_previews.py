"""Tests for agent camera previews from the agent's own snapshots (cameras/agent_previews.py).

Contract 1.1 lets an agent camera list "snapshot": it then serves a small
picture of its whole frame. The studio's preview of such a camera (the
picture with the crop window drawn on it, click-to-centre) uses it instead
of an OBS screenshot, so a camera node's cameras need no OBS source on the
studio. The fake agent's crop cameras serve a JPEG test card.
"""

from typing import Any

from baja_studio.fakes.blocky_jpeg import jpeg_size
from tests.fake_agents import StartAgent
from tests.modules.camera_studio import Studio, add_agent, agent_camera

LAPTOP_CROP = "laptop-crop"


def preview(studio: Studio, camera_id: str) -> Any:
    assert studio.http is not None
    return studio.http.get(f"/api/cameras/preview/{camera_id}", params={"width": 320})


def test_an_agent_crop_cameras_preview_is_its_snapshot_of_the_whole_frame(
    studio: Studio, start_agent: StartAgent
) -> None:
    start_agent(crop_cameras=1)
    add_agent(studio)
    address = {"agent_id": "laptop-a", "remote_camera_id": "crop1"}
    # No OBS source on the studio at all: the agent's snapshot is the picture.
    studio.configure(cameras=[agent_camera(id=LAPTOP_CROP, address=address)])
    studio.soon(lambda: studio.camera(LAPTOP_CROP)["capabilities"]["snapshot"] is True)

    answer = preview(studio, LAPTOP_CROP)

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.headers["x-preview-shows"] == "full"
    assert jpeg_size(answer.content) == (320, 176)


def test_a_ptz_without_snapshots_keeps_the_obs_route(
    studio: Studio, start_agent: StartAgent
) -> None:
    # The fake agent's PTZs list no snapshot: their preview is still their OBS
    # source, and without one the page is told to set it up, as before 1.1.
    start_agent()
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")

    assert studio.camera("trackside")["capabilities"]["snapshot"] is False
    answer = preview(studio, "trackside")
    assert answer.status_code == 404
    assert "no OBS source" in answer.json()["detail"]
