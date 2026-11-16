"""Tests for the feeds module's readiness checks (modules/feeds/readiness.py).

Each check gets a test for `ok` and one for its `warn` or `fail`, as the
conventions ask. The checks are called directly with the fakes from
feeds_studio.py; `state` is the module's published state, given by hand.
"""

from pathlib import Path
from typing import Any

from baja_studio.core.readiness import CheckOutcome, CheckStatus, ReadinessCheck
from baja_studio.modules.feeds.config import Feed, FeedsSettings
from baja_studio.modules.feeds.readiness import feeds_checks
from tests.modules.feeds_studio import FeedsFakes

DRONE = Feed(id="feed-drone", name="drone")
RECORDING = Feed(id="feed-laptop", name="laptop1", record=True)
FOLDER = Path("D:/recordings")


def run(
    fakes: FeedsFakes, settings: FeedsSettings, key: str, state: dict[str, Any] | None = None
) -> CheckOutcome:
    checks: list[ReadinessCheck] = feeds_checks(
        settings, fakes.system(), lambda: state or {}, FOLDER
    )
    check = next(item for item in checks if item.id == key)
    return check.run()


def test_mediamtx_answering_passes_with_its_version(feeds_fakes: FeedsFakes) -> None:
    outcome = run(feeds_fakes, FeedsSettings(), "mediamtx")
    assert outcome.status == CheckStatus.OK
    assert outcome.detail == "MediaMTX v1.21.3 answering on 127.0.0.1:9997"


def test_mediamtx_not_answering_fails(feeds_fakes: FeedsFakes) -> None:
    feeds_fakes.mediamtx.stop()
    outcome = run(feeds_fakes, FeedsSettings(), "mediamtx")
    assert outcome.status == CheckStatus.FAIL
    assert "no answer from MediaMTX" in outcome.detail


def test_ports_pass_while_mediamtx_runs_since_it_holds_them(feeds_fakes: FeedsFakes) -> None:
    # MediaMTX exits when it cannot open a port: if it answers, it has them all.
    outcome = run(feeds_fakes, FeedsSettings(), "ports")
    assert outcome.status == CheckStatus.OK
    assert outcome.detail.startswith("MediaMTX holds TCP 1935 (RTMP)")
    assert feeds_fakes.ports_asked == []  # no netstat needed


def test_a_port_held_by_another_program_fails_and_names_it(feeds_fakes: FeedsFakes) -> None:
    feeds_fakes.mediamtx.stop()
    feeds_fakes.taken_ports[("TCP", 1935)] = "obs64.exe (pid 4321)"
    outcome = run(feeds_fakes, FeedsSettings(), "ports")
    assert outcome.status == CheckStatus.FAIL
    assert "TCP 1935 (RTMP) is in use by obs64.exe (pid 4321)" in outcome.detail


def test_the_config_check_follows_the_last_apply(feeds_fakes: FeedsFakes) -> None:
    ok = run(feeds_fakes, FeedsSettings(), "config", {"config": {"error": ""}})
    assert ok.status == CheckStatus.OK
    refused = {"config": {"error": "UDP 8890 (SRT) is in use by vlc.exe"}}
    assert run(feeds_fakes, FeedsSettings(), "config", refused).status == CheckStatus.FAIL
    pending = {"config": {"error": "", "waiting_for_reload": True}}
    assert run(feeds_fakes, FeedsSettings(), "config", pending).status == CheckStatus.WARN


def test_every_feed_live_passes(feeds_fakes: FeedsFakes) -> None:
    state = {"feeds": [{"name": "drone", "status": "live"}]}
    outcome = run(feeds_fakes, FeedsSettings(feeds=[DRONE]), "live", state)
    assert outcome.status == CheckStatus.OK


def test_a_feed_not_up_yet_is_a_warning_not_a_failure(feeds_fakes: FeedsFakes) -> None:
    # Before the show the drone is often still on the ground: worth a look only.
    state = {"feeds": [{"name": "drone", "status": "waiting"}]}
    outcome = run(feeds_fakes, FeedsSettings(feeds=[DRONE]), "live", state)
    assert outcome.status == CheckStatus.WARN
    assert outcome.detail == "waiting for drone"


def test_feeds_are_not_checked_while_mediamtx_is_down(feeds_fakes: FeedsFakes) -> None:
    # One cause, one red line: the "mediamtx" check already fails.
    feeds_fakes.mediamtx.stop()
    outcome = run(feeds_fakes, FeedsSettings(feeds=[DRONE]), "live")
    assert outcome.status == CheckStatus.WARN
    assert outcome.detail.startswith("not checked")


def test_no_feeds_at_all_is_a_warning(feeds_fakes: FeedsFakes) -> None:
    assert run(feeds_fakes, FeedsSettings(), "live").status == CheckStatus.WARN


def test_the_disk_is_checked_only_when_a_feed_records(feeds_fakes: FeedsFakes) -> None:
    checks = feeds_checks(FeedsSettings(feeds=[DRONE]), feeds_fakes.system(), dict, FOLDER)
    assert "recordings_disk" not in [check.id for check in checks]
    outcome = run(feeds_fakes, FeedsSettings(feeds=[RECORDING]), "recordings_disk")
    assert outcome.status == CheckStatus.OK
    assert feeds_fakes.disk_asked == [FOLDER]


def test_a_nearly_full_recordings_disk_fails(feeds_fakes: FeedsFakes) -> None:
    feeds_fakes.free_disk = 5 * 1024**3
    outcome = run(feeds_fakes, FeedsSettings(feeds=[RECORDING]), "recordings_disk")
    assert outcome.status == CheckStatus.FAIL
    assert "GB free on the drive of feed recordings" in outcome.detail
