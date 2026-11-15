"""Tests for the fake MediaMTX (baja_studio/fakes/fake_mediamtx.py).

The fake must behave like the real MediaMTX v1.21.3 did when measured, or
the feeds module's tests would prove nothing. Each test here pins one of
those measured behaviours (listed in the fake's docstring), and the shapes
of its answers are compared with the recordings of the real binary.
"""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from baja_studio.fakes.fake_mediamtx import VERSION, FakeMediamtx
from baja_studio.modules.feeds.config import Feed, FeedsSettings
from baja_studio.modules.feeds.mediamtx_config import render_config, write_config
from tests.conftest import FakeClock

FIXTURES = Path(__file__).parents[1] / "modules" / "feeds_fixtures"
SALT = bytes(16)


class Setup:
    """A fake MediaMTX watching a config file the test writes from settings."""

    def __init__(self, tmp_path: Path, clock: FakeClock) -> None:
        self.tmp_path = tmp_path
        self.path = tmp_path / "mediamtx.yml"
        self.clock = clock
        self.write(FeedsSettings(feeds=[Feed(name="drone")]))
        self.fake = FakeMediamtx(clock, self.path)

    def write(self, settings: FeedsSettings) -> None:
        write_config(self.path, render_config(settings, self.tmp_path, SALT), force=True)

    def get(self, path: str) -> Any:
        response = self.fake.handle(httpx2.Request("GET", f"http://127.0.0.1:9997{path}"))
        return response.json()


@pytest.fixture
def setup(tmp_path: Path, fake_clock: FakeClock) -> Setup:
    return Setup(tmp_path, fake_clock)


def test_its_path_items_have_the_keys_of_the_real_ones(setup: Setup) -> None:
    # Same keys as the recording of the real MediaMTX, waiting and live.
    real = json.loads((FIXTURES / "paths_list_live.json").read_text(encoding="utf-8"))
    setup.fake.publish("drone")
    item = setup.get("/v3/paths/list")["items"][0]
    assert set(item) == set(real["items"][0])
    setup.fake.unpublish("drone")
    waiting = setup.get("/v3/paths/list")["items"][0]
    assert set(waiting) == set(real["items"][0])
    assert waiting["source"] is None and waiting["online"] is False


def test_only_configured_names_accept_a_sender_and_the_refusal_is_logged(setup: Setup) -> None:
    assert setup.fake.publish("drone") is True
    assert setup.fake.publish("dorne") is False
    assert setup.fake.log_lines[-1].endswith("closed: path 'dorne' is not configured")


def test_the_catch_all_accepts_any_name_under_its_own_name(setup: Setup) -> None:
    setup.write(FeedsSettings(accept_any_name=True))
    setup.clock.advance(5)
    assert setup.fake.publish("anything") is True
    item = setup.get("/v3/paths/list")["items"][0]
    assert (item["name"], item["confName"]) == ("anything", "all_others")


def test_a_password_protected_feed_needs_its_user_and_password(setup: Setup) -> None:
    setup.write(FeedsSettings(feeds=[Feed(name="laptop1", password="pw-1234")]))
    setup.clock.advance(5)
    assert setup.fake.publish("laptop1", "SRT") is False
    assert setup.fake.log_lines[-1].endswith("authentication failed")
    assert setup.fake.publish("laptop1", "SRT", user="laptop1", password="pw-1234") is True


def test_a_change_within_a_second_of_a_reload_is_never_loaded(setup: Setup) -> None:
    # Measured: two writes 0.3 s apart, and only the first reached MediaMTX.
    setup.fake.look_at_config_file()
    setup.write(FeedsSettings(feeds=[Feed(name="drone"), Feed(name="a")]))
    setup.fake.look_at_config_file()  # a reload
    setup.clock.advance(0.3)
    setup.write(FeedsSettings(feeds=[Feed(name="drone"), Feed(name="a"), Feed(name="b")]))
    setup.fake.look_at_config_file()  # inside the blind second: dropped
    setup.clock.advance(5)
    setup.fake.look_at_config_file()  # nothing new happened to the file since
    names = [item["name"] for item in setup.get("/v3/paths/list")["items"]]
    assert names == ["a", "drone"]


def test_a_reload_keeps_live_senders_on_unchanged_paths(setup: Setup) -> None:
    setup.fake.publish("drone")
    setup.write(FeedsSettings(feeds=[Feed(name="drone"), Feed(name="laptop1")]))
    setup.fake.look_at_config_file()
    assert setup.fake.reloads == 1
    drone = setup.get("/v3/paths/list")["items"][0]
    assert drone["online"] is True


def test_a_config_it_cannot_load_makes_it_exit(setup: Setup) -> None:
    setup.path.write_text("{ not json", encoding="utf-8")
    setup.fake.look_at_config_file()
    assert setup.fake.running is False
    with pytest.raises(httpx2.ConnectError):
        setup.get("/v3/info")


def test_a_busy_port_on_reload_makes_it_exit(setup: Setup) -> None:
    # Measured: rtmpAddress moved to a port another program held -> MediaMTX exited.
    setup.fake.occupy_port(2000)
    setup.write(FeedsSettings(feeds=[Feed(name="drone")], rtmp_port=2000))
    setup.fake.look_at_config_file()
    assert setup.fake.running is False
    assert "address already in use" in setup.fake.log_lines[-1]


def test_passwords_come_back_redacted(setup: Setup) -> None:
    setup.write(FeedsSettings(feeds=[Feed(name="laptop1", password="pw-1234")]))
    setup.clock.advance(5)
    users = setup.get("/v3/config/global/get")["authInternalUsers"]
    assert users[0]["pass"] == "<redacted>"


def test_info_answers_the_pinned_version(setup: Setup) -> None:
    assert setup.get("/v3/info")["version"] == "v1.21.3"


def test_the_fake_is_the_version_the_installer_bundles() -> None:
    # When the pin moves to a new MediaMTX, its behaviour must be measured
    # again and the fake updated: this test fails as a reminder.
    pin = json.loads(
        (Path(__file__).parents[3] / "installer" / "mediamtx" / "pin.json").read_text("utf-8")
    )
    assert pin["version"] == VERSION
