"""Tests for an `obs_crop` camera's OBS connection settings (cameras/crop_config.py, WP13).

`crop.obs` says which OBS holds the camera's source: playout's on this PC
(the default, as before WP13), or a remote OBS at host:port on a laptop.
"""

import pytest
from pydantic import ValidationError

from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_config import DEFAULT_OBS_PORT, ObsConnection, ObsCropSettings
from baja_studio.core.settings import secret_field_names


def test_by_default_a_crop_camera_uses_this_pcs_obs() -> None:
    # Settings saved before WP13 have no `obs`: they keep meaning playout's OBS.
    settings = ObsCropSettings.model_validate({"filter_name": "Baja crop"})

    assert settings.obs.remote is False
    assert settings.obs.key() is None
    assert settings.obs.port == DEFAULT_OBS_PORT == 4455


def test_a_remote_obs_needs_an_address() -> None:
    with pytest.raises(ValidationError, match="a remote OBS needs its address"):
        ObsConnection.model_validate({"remote": True, "host": "   "})


def test_an_address_is_kept_but_unused_while_remote_is_off() -> None:
    # Unticking "remote" keeps what was typed, so ticking it again brings it back.
    connection = ObsConnection.model_validate({"remote": False, "host": "192.168.10.50"})

    assert connection.host == "192.168.10.50"
    assert connection.key() is None


def test_the_connection_key_ignores_case_and_spaces_but_not_the_port() -> None:
    first = ObsConnection.model_validate({"remote": True, "host": " Laptop1 ", "port": 4455})
    second = ObsConnection.model_validate({"remote": True, "host": "laptop1", "port": 4456})

    assert first.key() == ("laptop1", 4455)
    assert second.key() == ("laptop1", 4456)


def test_the_password_is_a_secret_like_playouts() -> None:
    # Marked secret: masked in the API, left out of exports, scrubbed from diagnostics.
    assert secret_field_names(ObsConnection) == {"password"}


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        ObsConnection.model_validate({"remote": True, "host": "x", "pasword": "typo"})


def test_a_new_obs_rebuilds_the_driver_and_a_new_password_does_not() -> None:
    def camera(host: str, password: str) -> CameraConfig:
        obs = {"remote": True, "host": host, "password": password}
        return CameraConfig.model_validate(
            {"id": "lap", "driver": "obs_crop", "obs_source": "Cam", "crop": {"obs": obs}}
        )

    # Another laptop means another connection, so another driver...
    assert camera("a", "pw").connection_key() != camera("b", "pw").connection_key()
    # ...but a new password only redials the shared connection; the driver stays.
    assert camera("a", "pw").connection_key() == camera("a", "other").connection_key()
