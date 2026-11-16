"""Tests for studio-side presets of an agent's crop camera (cameras/drivers/agent_crop_presets.py).

A camera node's cameras are crop cameras, and the agent contract gives crop
cameras no presets of their own. So that the joystick's Call buttons, the
keys and the D200's preset buttons still work on them, the studio stores
their windows itself, in the camera's settings, as it does for an
`obs_crop` camera, and recalls one with `PUT /crop`.

The studio here runs with a fake agent that has one crop camera ("crop1");
the fake keeps its window's shape, as a camera node does.
"""

from baja_studio.agents.contract import CropRect
from baja_studio.cameras.config import CamerasSettings
from baja_studio.cameras.crop_config import CropPresetWindow
from baja_studio.cameras.drivers.agent_crop_presets import (
    keeps_studio_presets,
    recall_rect,
    stored_window,
)
from tests.fake_agents import StartAgent
from tests.modules.camera_studio import Studio, add_agent, agent_camera

LAPTOP_CROP = "laptop-crop"


def crop_camera_info(preset_max: int | None) -> dict[str, object]:
    return {"kind": "crop", "limits": {"preset_max": preset_max}}


def test_only_crop_cameras_whose_agent_keeps_no_presets_get_studio_presets() -> None:
    assert keeps_studio_presets(crop_camera_info(None)) is True
    # An agent that one day stores crop presets keeps its own.
    assert keeps_studio_presets(crop_camera_info(9)) is False
    assert keeps_studio_presets({"kind": "ptz", "limits": {"preset_max": None}}) is False
    assert keeps_studio_presets(None) is False  # /agent/info not read yet


def test_a_stored_window_is_the_crops_centre_and_width() -> None:
    window = stored_window(CropRect(x=0.1, y=0.2, w=0.5, h=0.5))

    assert (window.centre_x, window.centre_y, window.width) == (0.35, 0.45, 0.5)


def test_a_recalled_window_keeps_the_agents_shape_and_stays_inside() -> None:
    # The agent's window is twice as wide as tall (in frame fractions).
    current = CropRect(x=0.0, y=0.0, w=0.6, h=0.3)
    stored = CropPresetWindow(n=0, centre_x=0.9, centre_y=0.5, width=0.4)

    rect = recall_rect(stored, current)

    # Width 0.4, height 0.2 (the same shape), pushed left so it ends at the edge.
    assert rect == CropRect(x=0.6, y=0.4, w=0.4, h=0.2)


def crop(studio: Studio) -> dict[str, float]:
    return dict((studio.camera(LAPTOP_CROP) or {}).get("crop") or {})


def saved_windows(studio: Studio) -> list[CropPresetWindow]:
    settings = studio.services.settings.get("cameras")
    assert isinstance(settings, CamerasSettings)
    return settings.cameras[0].crop.windows


def test_presets_are_stored_in_the_studio_and_recalled_with_put_crop(
    studio: Studio, start_agent: StartAgent
) -> None:
    runner = start_agent(crop_cameras=1)
    add_agent(studio)
    address = {"agent_id": "laptop-a", "remote_camera_id": "crop1"}
    studio.configure(cameras=[agent_camera(id=LAPTOP_CROP, address=address)])
    studio.soon(lambda: crop(studio).get("w") == 1.0)

    # The camera now has presets 0-127, though its agent has none.
    assert studio.camera(LAPTOP_CROP)["capabilities"]["preset_max"] == 127

    # Someone moves the laptop's window to the left half (here: straight on the
    # fake agent); the studio reads the new window when the agent pushes it.
    half = CropRect(x=0.0, y=0.25, w=0.5, h=0.5)
    runner.run(lambda: runner.agent.camera("crop1").set_crop(runner.agent.now(), half, "test"))
    studio.soon(lambda: crop(studio).get("w") == 0.5)

    # Store preset 1 (number 0) from the studio.
    studio.command("store_preset", camera=LAPTOP_CROP, n=0)

    # It is saved in the camera's own settings, like an obs_crop camera's window.
    assert [stored.n for stored in saved_windows(studio)] == [0]
    assert saved_windows(studio)[0] == CropPresetWindow(n=0, centre_x=0.25, centre_y=0.5, width=0.5)

    # The operator puts the whole picture on, then recalls preset 1.
    studio.command("reset_crop", camera=LAPTOP_CROP)
    studio.soon(lambda: crop(studio).get("w") == 1.0)
    studio.command("recall_preset", camera=LAPTOP_CROP, n=0)

    # The agent got a PUT /crop of the stored window, and the studio shows it.
    studio.soon(lambda: crop(studio).get("w") == 0.5)
    assert runner.agent.camera("crop1").crop(runner.agent.now()) == half


def test_a_preset_that_is_not_stored_says_so(studio: Studio, start_agent: StartAgent) -> None:
    start_agent(crop_cameras=1)
    add_agent(studio)
    address = {"agent_id": "laptop-a", "remote_camera_id": "crop1"}
    studio.configure(cameras=[agent_camera(id=LAPTOP_CROP, address=address)])
    studio.soon(lambda: crop(studio).get("w") == 1.0)

    studio.command("recall_preset", camera=LAPTOP_CROP, n=4)

    studio.soon(lambda: studio.camera(LAPTOP_CROP)["last_error"] == "preset 5 is not stored")


def test_studio_presets_can_be_named_like_a_direct_cameras(
    studio: Studio, start_agent: StartAgent
) -> None:
    start_agent(crop_cameras=1)
    add_agent(studio)
    address = {"agent_id": "laptop-a", "remote_camera_id": "crop1"}
    studio.configure(cameras=[agent_camera(id=LAPTOP_CROP, address=address)])
    studio.soon(lambda: crop(studio).get("w") == 1.0)

    studio.command("rename_preset", camera=LAPTOP_CROP, n=2, label="Jump")

    # The name lives in the studio's settings, and the page sees it in the state.
    studio.soon(lambda: studio.camera(LAPTOP_CROP)["presets"] == [{"n": 2, "label": "Jump"}])
