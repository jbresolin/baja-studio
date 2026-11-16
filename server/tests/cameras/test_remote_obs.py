"""Tests for the pool that hands crop cameras their OBS (cameras/remote_obs.py, WP13, ADR 0021).

The pool decides, for each `obs_crop` camera, which OBS it talks to:
playout's (on this PC), or a connection to a laptop's OBS shared by every
camera that names the same address and port. Here the pool's connections
are never started: `start_worker` and `stop_worker` only record the names
they are given, so we can check exactly when a connection is opened and
closed. (The connections themselves are tested in
test_remote_obs_connection.py, and the whole thing in the service in
tests/modules/test_cameras_remote_obs.py.)
"""

import logging
from typing import Any

from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_config import ObsConnection
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway
from baja_studio.cameras.remote_obs import RemoteObsPool, remote_key
from baja_studio.cameras.remote_obs_connection import RemoteObsConnection
from baja_studio.core.supervisor import WorkerSpec
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock

PLAYOUTS_OBS = NoObsGateway("playout's OBS (a stand-in)")


class RecordingPool:
    """A pool whose connection workers are only recorded, never run."""

    def __init__(self, fake_clock: FakeClock) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.pool = RemoteObsPool(
            PLAYOUTS_OBS,
            fake_clock,
            InProcessObsNetwork().connect,
            lambda spec: self.started.append(spec.name),
            self.stopped.append,
            logging.getLogger("test"),
        )

    def lease(self, camera: CameraConfig) -> Any:
        return self.pool.lease(camera.id, camera.crop.obs)


def note_for(recording: RecordingPool, camera_id: str) -> str:
    """What Camera setup would say about a camera's OBS connection."""
    return str(recording.pool.describe(crop_camera(camera_id))["note"])


def crop_camera(
    camera_id: str, host: str | None = "laptop.test", password: str = "pw", **extra: Any
) -> CameraConfig:
    """An `obs_crop` camera; `host` None means playout's OBS (on this PC)."""
    obs: dict[str, Any] = {"remote": host is not None, "host": host or "", "password": password}
    return CameraConfig.model_validate(
        {"id": camera_id, "driver": "obs_crop", "obs_source": "Cam", "crop": {"obs": obs}, **extra}
    )


# --- which gateway --------------------------------------------------------------------------------


def test_a_camera_on_this_pcs_obs_gets_playouts_gateway_and_nothing_is_opened(
    fake_clock: FakeClock,
) -> None:
    recording = RecordingPool(fake_clock)

    gateway = recording.lease(crop_camera("local", host=None))

    assert gateway is PLAYOUTS_OBS
    assert recording.started == []


def test_two_cameras_naming_the_same_laptop_share_one_connection(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)

    first = recording.lease(crop_camera("lap-a"))
    # The same laptop typed with capitals is the same laptop.
    second = recording.lease(crop_camera("lap-b", host="Laptop.Test"))

    assert first is second
    assert isinstance(first, RemoteObsConnection)
    assert recording.started == ["remote-obs:laptop.test:4455"]


def test_another_port_on_the_same_laptop_is_another_obs(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)
    other_port = crop_camera("lap-b")
    other_port.crop.obs.port = 4456

    recording.lease(crop_camera("lap-a"))
    recording.lease(other_port)

    assert recording.started == ["remote-obs:laptop.test:4455", "remote-obs:laptop.test:4456"]


def test_the_connection_closes_when_its_last_camera_is_released(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)
    recording.lease(crop_camera("lap-a"))
    recording.lease(crop_camera("lap-b"))

    recording.pool.release("lap-a")
    assert recording.stopped == []  # lap-b still uses the laptop

    recording.pool.release("lap-b")
    assert recording.stopped == ["remote-obs:laptop.test:4455"]
    assert recording.pool.open_connections() == []


def test_releasing_a_camera_that_holds_nothing_does_nothing(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)

    recording.pool.release("never-leased")
    recording.lease(crop_camera("local", host=None))
    recording.pool.release("local")

    assert recording.stopped == []


def test_leasing_another_obs_gives_back_the_old_one_first(fake_clock: FakeClock) -> None:
    # The registry releases a camera before it builds its new driver; if it ever
    # did not, the pool still must not keep the old laptop's connection open.
    recording = RecordingPool(fake_clock)
    recording.lease(crop_camera("lap-a"))

    recording.lease(crop_camera("lap-a", host="laptop2.test"))

    assert recording.stopped == ["remote-obs:laptop.test:4455"]
    assert recording.started[-1] == "remote-obs:laptop2.test:4455"


def test_previews_and_commands_find_the_open_connection_without_a_lease(
    fake_clock: FakeClock,
) -> None:
    recording = RecordingPool(fake_clock)
    leased = recording.lease(crop_camera("lap-a"))

    assert recording.pool.gateway_for(crop_camera("x").crop.obs) is leased
    assert recording.pool.gateway_for(ObsConnection()) is PLAYOUTS_OBS
    unused = recording.pool.gateway_for(crop_camera("x", host="laptop9.test").crop.obs)
    assert unused.link().reason == (
        "OBS at laptop9.test:4455: no enabled camera uses it yet (save the camera)"
    )
    assert recording.started == ["remote-obs:laptop.test:4455"]  # nothing opened by looking


def test_remote_key_is_none_for_playouts_obs_and_other_drivers() -> None:
    ptz = CameraConfig.model_validate({"id": "ptz", "address": {"host": "10.0.0.9"}})

    assert remote_key(ptz) is None
    assert remote_key(crop_camera("local", host=None)) is None
    assert remote_key(crop_camera("lap", host="Laptop.Test")) == ("laptop.test", 4455)


# --- passwords: the camera saved last wins ------------------------------------------------------


def test_with_one_password_for_a_laptop_there_is_nothing_to_say(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)
    cameras = [crop_camera("lap-a"), crop_camera("lap-b")]

    recording.pool.choose_passwords(cameras)

    assert recording.pool.password("laptop.test", 4455) == "pw"
    assert note_for(recording, "lap-a") == note_for(recording, "lap-b") == ""


def test_the_camera_whose_password_changed_gives_it_to_the_connection(
    fake_clock: FakeClock,
) -> None:
    recording = RecordingPool(fake_clock)
    recording.pool.choose_passwords([crop_camera("lap-a"), crop_camera("lap-b")])
    connection = recording.lease(crop_camera("lap-a"))

    # Camera A is saved with a new password; B still has the old one.
    recording.pool.choose_passwords([crop_camera("lap-a", password="new"), crop_camera("lap-b")])

    assert recording.pool.password("laptop.test", 4455) == "new"
    assert connection._current_password() == "new"  # the open connection redials with it
    assert note_for(recording, "lap-a") == ""
    assert note_for(recording, "lap-b") == (
        "camera 'lap-a' was saved later with another password for OBS at "
        "laptop.test:4455, and the connection uses that one: save the same password on "
        "both cameras"
    )


def test_an_unchanged_camera_does_not_take_the_password_back(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)
    recording.pool.choose_passwords([crop_camera("lap-a"), crop_camera("lap-b")])
    recording.pool.choose_passwords([crop_camera("lap-a", password="new"), crop_camera("lap-b")])

    # Another save where neither password changes (a label edit on B).
    recording.pool.choose_passwords(
        [crop_camera("lap-a", password="new"), crop_camera("lap-b", label="B")]
    )

    assert recording.pool.password("laptop.test", 4455) == "new"


def test_when_the_cameras_password_owner_leaves_another_cameras_is_used(
    fake_clock: FakeClock,
) -> None:
    recording = RecordingPool(fake_clock)
    recording.pool.choose_passwords([crop_camera("lap-a", password="a"), crop_camera("lap-b")])
    recording.pool.choose_passwords([crop_camera("lap-a", password="a2"), crop_camera("lap-b")])

    # Camera A (whose password was in use) is removed.
    recording.pool.choose_passwords([crop_camera("lap-b")])

    assert recording.pool.password("laptop.test", 4455) == "pw"
    assert note_for(recording, "lap-b") == ""


def test_disabled_cameras_and_cameras_on_this_pc_take_no_part(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)

    recording.pool.choose_passwords(
        [
            crop_camera("lap-a"),
            crop_camera("lap-b", password="other", enabled=False),
            crop_camera("local", host=None, password="ignored"),
        ]
    )

    assert recording.pool.password("laptop.test", 4455) == "pw"
    assert note_for(recording, "lap-a") == ""


def test_a_new_connection_starts_with_the_chosen_password(fake_clock: FakeClock) -> None:
    recording = RecordingPool(fake_clock)
    recording.pool.choose_passwords([crop_camera("lap-a", password="a"), crop_camera("lap-b")])

    # On the first settings seen, every camera is "new": the last one in the list wins.
    connection = recording.lease(crop_camera("lap-a", password="a"))

    assert connection._current_password() == "pw"


# --- what the pages see --------------------------------------------------------------------------


def test_a_camera_is_described_by_where_its_obs_is_never_by_its_password(
    fake_clock: FakeClock,
) -> None:
    recording = RecordingPool(fake_clock)

    remote = recording.pool.describe(crop_camera("lap-a", password="hush"))
    local = recording.pool.describe(crop_camera("local", host=None))

    assert remote == {"remote": True, "address": "laptop.test:4455", "note": ""}
    assert local == {"remote": False, "address": None, "note": ""}
    assert "hush" not in str(remote)


def test_the_pool_for_a_module_uses_that_modules_workers(fake_clock: FakeClock) -> None:
    started: list[WorkerSpec] = []

    class Context:
        clock = fake_clock
        logger = logging.getLogger("test")

        def start_worker(self, spec: WorkerSpec) -> None:
            started.append(spec)

        def stop_worker(self, name: str) -> None:
            pass

    pool = RemoteObsPool.for_module(Context(), PLAYOUTS_OBS)  # type: ignore[arg-type]
    pool.lease("lap-a", crop_camera("lap-a").crop.obs)

    assert [spec.name for spec in started] == ["remote-obs:laptop.test:4455"]
    assert started[0].starts_offline  # not "ok" before the laptop has answered
