"""Tests for applying the feeds settings (modules/feeds/controller.py).

The controller is the only writer of `mediamtx.yml`. These tests drive it
directly, on the fake clock, with a fake MediaMTX reading the same file,
and check the three precautions learned from the real MediaMTX:

1. a port the new config adds is checked first (MediaMTX exits on a busy port);
2. writes are at least two seconds apart (MediaMTX ignores a second change
   within a second of a reload);
3. every write is checked through the fingerprint MediaMTX reports, and
   written again, then the service restarted, if MediaMTX did not take it.
"""

import logging
from pathlib import Path

import httpx2
import pytest

from baja_studio.core.system_services import FEEDS_SERVICE, NOT_INSTALLED
from baja_studio.fakes.fake_mediamtx import FakeMediamtx
from baja_studio.modules.feeds.config import Feed, FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxApiClient
from baja_studio.modules.feeds.controller import (
    MAX_REWRITES,
    MIN_WRITE_GAP_SECONDS,
    VERIFY_SECONDS,
    FeedsConfigController,
    FeedsConfigError,
)
from baja_studio.modules.feeds.mediamtx_config import config_path, parse_config
from baja_studio.modules.feeds.ports import FeedPort, FeedPortHolder
from tests.conftest import FakeClock
from tests.modules.gateway_studio import FakeServiceControl

LOGGER = logging.getLogger("test.feeds.controller")
DRONE = FeedsSettings(feeds=[Feed(id="feed-drone", name="drone")])
DRONE_AND_LAPTOP = FeedsSettings(
    feeds=[Feed(id="feed-drone", name="drone"), Feed(id="feed-laptop", name="laptop1")]
)


class Bench:
    """A controller, the fake MediaMTX watching its file, and the ports other programs hold."""

    def __init__(self, data_directory: Path, clock: FakeClock) -> None:
        self.clock = clock
        self.services = FakeServiceControl()
        self.services.statuses[FEEDS_SERVICE] = "SERVICE_RUNNING"
        self.taken: dict[tuple[str, int], str] = {}
        self.asked: list[list[FeedPort]] = []
        self.controller = FeedsConfigController(
            data_directory, self.services, self.holders, clock, LOGGER
        )
        self.path = config_path(data_directory)
        self.mediamtx = FakeMediamtx(clock, self.path)
        self.api = MediamtxApiClient("127.0.0.1:9997", self.mediamtx.transport())

    def holders(self, ports: list[FeedPort]) -> list[FeedPortHolder]:
        self.asked.append(list(ports))
        return [
            FeedPortHolder(port, self.taken[(port.protocol, port.port)])
            for port in ports
            if (port.protocol, port.port) in self.taken
        ]

    def verify(self) -> str:
        """What the worker does at each poll: read the running config and compare."""
        return self.controller.verify(self.api.global_config())

    def later(self, seconds: float) -> None:
        self.clock.advance(seconds)


@pytest.fixture
def bench(data_directory: Path, fake_clock: FakeClock) -> Bench:
    return Bench(data_directory, fake_clock)


def test_the_first_apply_writes_the_file_and_mediamtx_confirms_it(bench: Bench) -> None:
    assert bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False) is True
    assert "drone" in parse_config(bench.path.read_text(encoding="utf-8"))["paths"]
    # Until MediaMTX reports the new fingerprint, the controller waits for it.
    assert bench.controller.waiting_for_reload is True
    assert bench.verify() == ""
    assert bench.controller.waiting_for_reload is False


def test_the_same_settings_again_write_nothing(bench: Bench) -> None:
    # A studio restart re-applies the same settings: MediaMTX must not be disturbed.
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    bench.verify()
    before = bench.path.stat().st_mtime_ns
    bench.later(MIN_WRITE_GAP_SECONDS)
    assert bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=True) is False
    assert bench.path.stat().st_mtime_ns == before
    assert bench.controller.waiting_for_reload is False


def test_writes_are_held_back_until_two_seconds_after_the_last(bench: Bench) -> None:
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    # Right after a write, the next must wait the whole gap...
    assert bench.controller.seconds_until_write_allowed() == pytest.approx(MIN_WRITE_GAP_SECONDS)
    bench.later(0.5)
    assert bench.controller.seconds_until_write_allowed() == pytest.approx(1.5)
    # ...and then it is allowed.
    bench.later(1.5)
    assert bench.controller.seconds_until_write_allowed() == 0.0


def test_a_new_port_held_by_another_program_is_refused_and_nothing_is_written(
    bench: Bench,
) -> None:
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    before = bench.path.read_text(encoding="utf-8")
    bench.taken[("TCP", 1940)] = "nginx.exe (pid 99)"
    bench.later(MIN_WRITE_GAP_SECONDS)

    with pytest.raises(FeedsConfigError, match=r"TCP 1940 \(RTMP\) is in use by nginx.exe"):
        bench.controller.apply_if_needed(
            DRONE.model_copy(update={"rtmp_port": 1940}), mediamtx_on_api_port=True
        )
    # The file MediaMTX reads is untouched, so it never tries the busy port.
    assert bench.path.read_text(encoding="utf-8") == before


def test_only_ports_the_new_config_adds_are_checked(bench: Bench) -> None:
    # The ports in the current file are MediaMTX's own: asking who holds them
    # would find MediaMTX itself. Only the moved SRT port is looked at.
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    bench.later(MIN_WRITE_GAP_SECONDS)
    bench.asked.clear()
    bench.controller.apply_if_needed(
        DRONE.model_copy(update={"srt_port": 9100}), mediamtx_on_api_port=True
    )
    assert [[(port.protocol, port.port) for port in asked] for asked in bench.asked] == [
        [("UDP", 9100)]
    ]


def test_a_mediamtx_already_answering_on_the_api_port_is_not_a_clash(bench: Bench) -> None:
    # The first write on a PC where MediaMTX already runs on the API port
    # (the rehearsal rig's fake, or a file lost): that port is MediaMTX's.
    bench.taken[("TCP", 9997)] = "mediamtx (the one we talk to)"
    assert bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=True) is True


def test_a_reload_mediamtx_missed_is_written_again(bench: Bench) -> None:
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    bench.mediamtx.look_at_config_file()  # MediaMTX starts with this file
    bench.later(MIN_WRITE_GAP_SECONDS)
    # Something else touches the file (an editor, an antivirus): MediaMTX reloads...
    bench.path.write_text(bench.path.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
    bench.mediamtx.look_at_config_file()
    # ...and the studio's change, in the same second, falls in its blind spot.
    bench.controller.apply_if_needed(DRONE_AND_LAPTOP, mediamtx_on_api_port=True)
    bench.mediamtx.look_at_config_file()
    assert not bench.mediamtx.publish("laptop1")
    assert bench.verify() == ""  # too early to tell

    # After VERIFY_SECONDS the fingerprint still does not match: written again,
    # and this time MediaMTX takes it. No restart was needed.
    bench.later(VERIFY_SECONDS)
    assert bench.verify() == ""
    assert bench.mediamtx.publish("laptop1")
    assert bench.verify() == ""
    assert bench.controller.waiting_for_reload is False
    assert bench.services.restarts == []


def test_the_service_is_restarted_when_rewriting_does_not_help(bench: Bench) -> None:
    # A MediaMTX whose file watcher is broken: it never reloads anything.
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    bench.mediamtx.stop()  # stopped: reads no file, answers nothing...
    stale_config = {"sysLogPrefix": "baja-studio-old"}
    for _ in range(MAX_REWRITES):
        bench.later(VERIFY_SECONDS)
        assert bench.controller.verify(stale_config) == ""  # ...so: write again
    bench.later(VERIFY_SECONDS)
    problem = bench.controller.verify(stale_config)
    assert problem == "MediaMTX did not reload its config: the feeds service was restarted"
    assert bench.services.restarts == [FEEDS_SERVICE]


def test_without_an_installed_service_the_problem_is_reported_not_fixed(bench: Bench) -> None:
    bench.services.statuses[FEEDS_SERVICE] = NOT_INSTALLED
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    stale_config = {"sysLogPrefix": "baja-studio-old"}
    for _ in range(MAX_REWRITES + 1):
        bench.later(VERIFY_SECONDS)
        problem = bench.controller.verify(stale_config)
    assert "no service to restart" in problem
    assert bench.services.restarts == []


def test_a_mediamtx_that_does_not_answer_is_not_verified(bench: Bench) -> None:
    # The worker only calls verify while MediaMTX answers; when it starts, it
    # reads the file afresh and the fingerprint matches at once.
    bench.controller.apply_if_needed(DRONE, mediamtx_on_api_port=False)
    bench.mediamtx.stop()
    with pytest.raises(httpx2.ConnectError):
        bench.mediamtx.handle(httpx2.Request("GET", "http://127.0.0.1:9997/v3/info"))
    bench.later(30.0)
    bench.mediamtx.start()
    assert bench.verify() == ""
    assert bench.controller.waiting_for_reload is False
