"""Tests for an agent crop camera's window (cameras/drivers/agent_crop.py), with the fake agent.

A laptop agent can own a `kind=crop` camera: a digital window on its own
sensor. The studio shows that window on the preview like an OBS crop
camera's, so it reads `GET /crop` when the window moves, and click-to-centre
becomes a `PUT /crop`. The fake agent here has one crop camera ("crop1").
"""

import pytest

from baja_studio.agents.contract import CropRect
from baja_studio.cameras.crop_window import WindowTarget
from baja_studio.cameras.drivers.agent_crop import agent_crop_state, target_rect
from baja_studio.core.module import CommandError
from tests.fake_agents import StartAgent
from tests.modules.camera_studio import Studio, add_agent, agent_camera

LAPTOP_CROP = "laptop-crop"


def window(studio: Studio) -> dict[str, float]:
    return dict((studio.camera(LAPTOP_CROP) or {}).get("crop") or {})


def test_a_jump_keeps_the_windows_size_and_stays_inside_the_frame() -> None:
    half = CropRect(x=0.25, y=0.25, w=0.5, h=0.5)

    moved = target_rect(half, WindowTarget(0.4, 0.5))
    assert moved.x == pytest.approx(0.15)
    assert (moved.y, moved.w, moved.h) == (0.25, 0.5, 0.5)
    # A click in the corner centres the window as near as it fits.
    assert target_rect(half, WindowTarget(0.0, 1.0)) == CropRect(x=0.0, y=0.5, w=0.5, h=0.5)
    assert target_rect(half, WindowTarget(whole=True)) == CropRect(x=0.0, y=0.0, w=1.0, h=1.0)


def test_the_published_window_has_the_same_keys_as_an_obs_crop_cameras() -> None:
    state = agent_crop_state(CropRect(x=0.25, y=0.25, w=0.5, h=0.5), zoom=0.55)

    assert state == {
        "x": 0.25,
        "y": 0.25,
        "w": 0.5,
        "h": 0.5,
        "centre_x": 0.5,
        "centre_y": 0.5,
        "zoom": 0.55,
        "magnification": 2.0,
    }


def test_the_window_is_read_moved_and_centred_from_the_studio(
    studio: Studio, start_agent: StartAgent
) -> None:
    runner = start_agent(crop_cameras=1)
    add_agent(studio)
    address = {"agent_id": "laptop-a", "remote_camera_id": "crop1"}
    studio.configure(cameras=[agent_camera(id=LAPTOP_CROP, address=address)])

    # Once the link is up, the window is read: the fake starts with the whole frame.
    studio.soon(lambda: window(studio).get("w") == 1.0)

    # The page zooms in: the pushed position changes, so the window is read again.
    studio.command("move", source="web:a", camera=LAPTOP_CROP, zoom=1.0, ttl_ms=600)
    studio.soon(lambda: 0 < window(studio).get("w", 1.0) < 0.9)

    # A click on the left edge of the preview: the window goes as far left as it can.
    studio.command("centre_window", source="web:a", camera=LAPTOP_CROP, x=0.0, y=0.5)
    studio.soon(lambda: window(studio).get("x") == 0.0)
    assert runner.agent.camera("crop1").crop(runner.agent.now()).x == 0.0


def test_a_ptz_cannot_be_centred_like_a_crop(studio: Studio, start_agent: StartAgent) -> None:
    start_agent()
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")

    with pytest.raises(CommandError, match="not a crop camera"):
        studio.command("centre_window", camera="trackside", x=0.5, y=0.5)
