"""Tests for Camera setup's short connections to a remote OBS (cameras/remote_obs_probe.py).

"Test connection" and the source list of a laptop no camera uses yet open a
connection of their own for one question and close it. The laptop is a
fake OBS on the in-process network; we check the answers the page shows and
that no connection is left open.
"""

import pytest

from baja_studio.cameras.remote_obs_probe import (
    CONNECTED,
    UNREACHABLE,
    WRONG_PASSWORD,
    ObsProbeError,
    probe_obs,
    temporary_client,
)
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock

HOST, PORT, PASSWORD = "laptop.test", 4455, "laptop-secret"


@pytest.fixture
def laptop(fake_clock: FakeClock) -> tuple[FakeObs, InProcessObsNetwork]:
    obs = FakeObs(fake_clock, password=PASSWORD)
    obs.add_video_input("Laptop cam A", 3840, 2160)
    network = InProcessObsNetwork()
    network.plug(HOST, PORT, obs)
    return obs, network


def test_the_right_password_connects_and_reports_obss_versions(
    fake_clock: FakeClock, laptop: tuple[FakeObs, InProcessObsNetwork]
) -> None:
    obs, network = laptop

    result = probe_obs(HOST, PORT, PASSWORD, fake_clock, network.connect)

    assert result.outcome == CONNECTED
    assert (result.obs_version, result.websocket_version) == ("32.0.0-fake", "5.6.0")
    assert result.as_reply()["result"] == "connected"
    assert obs.session_count == 0  # closed again


def test_a_wrong_or_missing_password_is_told_apart_from_an_unreachable_obs(
    fake_clock: FakeClock, laptop: tuple[FakeObs, InProcessObsNetwork]
) -> None:
    obs, network = laptop

    wrong = probe_obs(HOST, PORT, "nope", fake_clock, network.connect)
    missing = probe_obs(HOST, PORT, "", fake_clock, network.connect)
    nobody = probe_obs("nowhere.test", PORT, PASSWORD, fake_clock, network.connect)

    assert (wrong.outcome, wrong.message) == (
        WRONG_PASSWORD,
        "OBS at laptop.test:4455 refused the password",
    )
    assert (missing.outcome, missing.message) == (
        WRONG_PASSWORD,
        "OBS at laptop.test:4455 asks for a password: type it above",
    )
    assert (nobody.outcome, nobody.message) == (
        UNREACHABLE,
        "OBS at nowhere.test:4455 unreachable: connection refused",
    )
    # Nothing in any answer repeats the password that was tried.
    assert "nope" not in wrong.message and PASSWORD not in nobody.message
    assert obs.session_count == 0


def test_a_temporary_client_is_closed_even_when_the_question_fails(
    fake_clock: FakeClock, laptop: tuple[FakeObs, InProcessObsNetwork]
) -> None:
    obs, network = laptop

    with (
        pytest.raises(RuntimeError),
        temporary_client(HOST, PORT, PASSWORD, fake_clock, network.connect) as client,
    ):
        assert [item.name for item in client.get_input_list()] == ["Laptop cam A"]
        assert obs.session_count == 1
        raise RuntimeError("the caller failed halfway")

    assert obs.session_count == 0


def test_a_temporary_client_that_cannot_connect_raises_a_probe_error(
    fake_clock: FakeClock, laptop: tuple[FakeObs, InProcessObsNetwork]
) -> None:
    _, network = laptop

    with (
        pytest.raises(ObsProbeError) as refused,
        temporary_client(HOST, PORT, "nope", fake_clock, network.connect),
    ):
        pass

    assert refused.value.outcome == WRONG_PASSWORD
