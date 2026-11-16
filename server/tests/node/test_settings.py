"""Tests for the camera node's settings (node/settings.py) and how a camera becomes a crop camera.

The node's settings say which cameras it serves and how. Each camera is
turned into the studio's own camera settings (`node/backends.py`), so the
studio's crop driver and crop motion can run it unchanged.
"""

import pytest
from pydantic import ValidationError

from baja_studio.cameras.config import DriverKind
from baja_studio.node.backends import backend_key, crop_settings, obs_camera_config
from baja_studio.node.settings import NODE_PORT, NodeCameraSettings, NodeSettings, VideoMode
from baja_studio.node.test_cameras import fake_camera_settings


def test_a_fresh_node_listens_on_8891_and_serves_nothing() -> None:
    # 8890 is the MV app's zone agent; both may run on one laptop.
    settings = NodeSettings()

    assert settings.port == NODE_PORT == 8891
    assert settings.served() == []
    assert settings.token == ""


def test_two_cameras_cannot_share_an_id_or_a_usb_device() -> None:
    usb = {"path": r"\\?\usb#one", "name": "S600"}
    with pytest.raises(ValidationError, match="same id"):
        NodeSettings(cameras=[NodeCameraSettings(id="cam1"), NodeCameraSettings(id="cam1")])
    with pytest.raises(ValidationError, match="same USB device"):
        NodeSettings.model_validate(
            {"cameras": [{"id": "cam1", "usb_device": usb}, {"id": "cam2", "usb_device": usb}]}
        )


def test_a_disabled_camera_is_not_served() -> None:
    settings = NodeSettings(
        cameras=[NodeCameraSettings(id="cam1"), NodeCameraSettings(id="cam2", enabled=False)]
    )

    assert [camera.id for camera in settings.served()] == ["cam1"]


def test_an_encoder_is_auto_or_a_plain_name() -> None:
    assert NodeCameraSettings.model_validate({"id": "c", "video": {"encoder": "h264_qsv"}})
    with pytest.raises(ValidationError):
        NodeCameraSettings.model_validate({"id": "c", "video": {"encoder": "x; rm -rf"}})


def test_an_obs_mode_camera_becomes_an_obs_crop_camera_on_this_laptops_obs() -> None:
    camera = NodeCameraSettings.model_validate(
        {
            "id": "cam2",
            "mode": "obs",
            "min_window": 0.6,
            "obs": {"port": 4460, "source": "S600 A", "filter_name": "Crop"},
        }
    )

    config = obs_camera_config(camera)

    assert config is not None
    assert config.driver == DriverKind.OBS_CROP
    assert config.obs_source == "S600 A"
    assert (config.crop.obs.remote, config.crop.obs.host, config.crop.obs.port) == (
        True,
        "127.0.0.1",
        4460,
    )
    assert (config.crop.filter_name, config.crop.min_window) == ("Crop", 0.6)


def test_an_obs_mode_camera_without_a_source_has_no_crop_camera_yet() -> None:
    camera = NodeCameraSettings(id="cam2", mode=VideoMode.OBS)

    assert obs_camera_config(camera) is None
    # Its speeds and limits are still there for the shared crop motion.
    assert crop_settings(camera).pan_speed == camera.pan_speed


def test_a_new_backend_is_needed_only_when_the_video_changes() -> None:
    camera = fake_camera_settings(1)[0]
    relabelled = camera.model_copy(update={"label": "Finish"})
    faster = camera.model_copy(update={"pan_speed": 0.6})
    other_bitrate = camera.model_copy(
        update={"video": camera.video.model_copy(update={"bitrate_kbps": 6000})}
    )

    # A label or a speed is read by the running driver; a bitrate means a new pipeline.
    assert backend_key(relabelled, "ffmpeg") == backend_key(camera, "ffmpeg")
    assert backend_key(faster, "ffmpeg") == backend_key(camera, "ffmpeg")
    assert backend_key(other_bitrate, "ffmpeg") != backend_key(camera, "ffmpeg")
    # And so does another video mode.
    obs = camera.model_copy(update={"mode": VideoMode.OBS})
    assert backend_key(obs, "ffmpeg") != backend_key(camera, "ffmpeg")


def test_the_decoder_is_auto_by_default_and_the_rockchip_one_can_be_chosen() -> None:
    # auto: the Rockchip hardware path on the NanoPi, the CPU on a laptop (ADR 0024).
    assert NodeCameraSettings(id="cam1").video.decoder == "auto"
    chosen = NodeCameraSettings(id="cam1", video={"decoder": "mjpeg_rkmpp"})  # type: ignore[arg-type]
    assert chosen.video.decoder == "mjpeg_rkmpp"
    with pytest.raises(ValidationError):
        NodeCameraSettings(id="cam1", video={"decoder": "mjpeg_vaapi"})  # type: ignore[arg-type]
