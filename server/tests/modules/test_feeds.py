"""Tests for the feeds module (modules/feeds), running inside the service.

Each test starts an app with only the feeds module, on the fake clock, with
the fakes from tests/modules/feeds_studio.py standing in for MediaMTX, NSSM,
Windows' port list and the disk. The fake MediaMTX reads the very
`mediamtx.yml` the module writes into the test's data folder, so these
tests see the whole loop: settings -> file -> MediaMTX reloads -> state.

The module's worker polls every two seconds; tests move fake time with
`step_fake_time` to make a poll happen.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.core.module import CommandError
from baja_studio.core.services import StudioServices
from baja_studio.core.settings_export import export_document
from baja_studio.core.supervisor import HealthState
from baja_studio.core.system_services import FEEDS_SERVICE
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.modules.feeds import FeedsModule
from baja_studio.modules.feeds.mediamtx_config import config_path, parse_config
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time,
)
from tests.modules.feeds_studio import LAN_ADDRESS, ConnectedGateway, FeedsFakes

POLL = 2.0
DRONE = {"name": "drone", "label": "Drone", "kind": "drone"}
LAPTOP = {"name": "laptop1", "label": "Laptop 1", "kind": "laptop"}
PASSWORD = "s3cret-Feed.pw"


@pytest.fixture
def services(make_app: AppFactory, feeds_fakes: FeedsFakes) -> Iterator[StudioServices]:
    """An app with just the feeds module, its outside world faked."""
    app = make_app(lambda registry: [FeedsModule(feeds_fakes.services, feeds_fakes.system())])
    services: StudioServices = app.state.services
    with TestClient(app):
        eventually(lambda: health(services)[0] != HealthState.OFFLINE)
        yield services


def health(services: StudioServices) -> tuple[HealthState, str]:
    current = services.supervisor.module_health("feeds")
    return current.state, current.message


def state(services: StudioServices, path: str = "") -> Any:
    return services.state.get("feeds", path)


def poll(fake_clock: FakeClock, times: int = 1) -> None:
    """Let `times` poll intervals pass, the worker asleep before and after each."""
    for _ in range(times):
        step_fake_time(fake_clock, POLL, module_worker_threads("feeds"), step=POLL)


def card(services: StudioServices, name: str) -> dict[str, Any]:
    return next(item for item in state(services, "feeds") if item["name"] == name)


def written_config(data_directory: Path) -> dict[str, Any]:
    return parse_config(config_path(data_directory).read_text(encoding="utf-8"))


def test_on_start_the_config_is_written_and_mediamtx_is_watched(
    services: StudioServices, feeds_fakes: FeedsFakes, data_directory: Path
) -> None:
    # The module writes mediamtx.yml as soon as it starts; the fake MediaMTX
    # reads that file. HLS must be off: its default port is the studio's 8888.
    document = written_config(data_directory)
    assert document["hls"] is False
    assert document["apiAddress"] == "127.0.0.1:9997"
    # The worker asked MediaMTX and published what it said.
    assert state(services, "service.answering") is True
    assert state(services, "service.version") == "v1.21.3"
    assert state(services, "service.status") == "SERVICE_RUNNING"
    assert health(services) == (HealthState.OK, "")


def test_a_new_feed_is_written_and_shows_as_waiting_with_its_urls(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock, data_directory: Path
) -> None:
    # The operator adds the drone on the Feeds page (a settings save).
    services.settings.update("feeds", {"feeds": [DRONE]})
    poll(fake_clock)

    # The file now has a path for it, and the fake MediaMTX loaded it.
    assert "drone" in written_config(data_directory)["paths"]
    drone = card(services, "drone")
    assert drone["status"] == "waiting"
    # The URL the drone's app needs uses this PC's LAN address and RTMP's port.
    assert drone["publish_urls"][0] == {
        "protocol": "RTMP",
        "address": LAN_ADDRESS,
        "url": f"rtmp://{LAN_ADDRESS}:1935/drone",
    }
    assert drone["obs_url"] == "rtsp://127.0.0.1:8554/drone"


def test_a_publishing_drone_shows_live_with_size_codec_bitrate_and_readers(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("feeds", {"feeds": [DRONE]})
    poll(fake_clock)

    # The drone starts pushing 1280x720 over RTMP, and OBS reads it.
    assert feeds_fakes.mediamtx.publish("drone", "RTMP", size=(1280, 720))
    feeds_fakes.mediamtx.add_reader("drone")
    poll(fake_clock)
    # Over the next two seconds it sends 1 MB: 1,000,000 bytes x 8 bits / 2 s = 4000 kbit/s.
    feeds_fakes.mediamtx.receive_bytes("drone", 1_000_000)
    poll(fake_clock)

    drone = card(services, "drone")
    assert drone["status"] == "live"
    assert drone["protocol"] == "RTMP"
    assert (drone["width"], drone["height"]) == (1280, 720)
    assert drone["video_codec"] == "H264"
    assert drone["readers"] == 1
    assert drone["bitrate_kbps"] == 4000


def test_a_sender_on_an_unknown_name_is_listed_as_refused(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    # Someone types "dorne" in the drone app. MediaMTX refuses it and says so
    # only in its log, which the module reads.
    services.settings.update("feeds", {"feeds": [DRONE]})
    poll(fake_clock)
    assert not feeds_fakes.mediamtx.publish("dorne")
    poll(fake_clock)

    refused = state(services, "refused")
    assert [item["name"] for item in refused] == ["dorne"]


def test_mediamtx_down_makes_the_module_offline_and_shows_its_log(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    # The service stops (crash, or someone stopped it in Services).
    feeds_fakes.extra_log_lines.append("2026/10/01 09:00:00 ERR something went wrong")
    feeds_fakes.services.statuses[FEEDS_SERVICE] = "SERVICE_STOPPED"
    feeds_fakes.mediamtx.stop()
    poll(fake_clock)

    # The home card goes offline with the reason in operator words, and the
    # page gets MediaMTX's last log lines to show.
    assert health(services) == (HealthState.OFFLINE, "the feeds service is stopped")
    assert state(services, "service.answering") is False
    assert state(services, "service.log_tail")[-1].endswith("something went wrong")

    # It comes back: the next poll sees it.
    feeds_fakes.services.statuses[FEEDS_SERVICE] = "SERVICE_RUNNING"
    feeds_fakes.mediamtx.start()
    poll(fake_clock)
    assert health(services) == (HealthState.OK, "")
    assert state(services, "service.log_tail") == []


def test_a_program_on_a_feed_port_is_named_while_mediamtx_is_down(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    # Another program holds RTMP's port, so MediaMTX could not start.
    feeds_fakes.taken_ports[("TCP", 1935)] = "otherserver.exe (pid 777)"
    feeds_fakes.mediamtx.stop()
    poll(fake_clock)

    state_now, message = health(services)
    assert state_now == HealthState.OFFLINE
    assert "TCP 1935 (RTMP) is in use by otherserver.exe (pid 777)" in message
    assert state(services, "service.port_conflicts")[0]["port"] == 1935


def test_a_port_change_to_a_taken_port_is_refused_and_nothing_is_written(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock, data_directory: Path
) -> None:
    # MediaMTX exits when a reload asks for a busy port, which would stop every
    # feed. So the module checks the new port first and refuses the change.
    before = config_path(data_directory).read_text(encoding="utf-8")
    feeds_fakes.take_port("UDP", 9000, "vlc.exe (pid 55)")
    services.settings.update("feeds", {"srt_port": 9000})
    poll(fake_clock)

    assert config_path(data_directory).read_text(encoding="utf-8") == before
    assert feeds_fakes.mediamtx.running  # it never saw the bad port
    assert "UDP 9000 (SRT) is in use by vlc.exe (pid 55)" in state(services, "config.error")
    assert health(services)[0] == HealthState.DEGRADED

    # Still taken half a minute later: still refused (it is tried again by itself).
    poll(fake_clock, times=16)
    assert "UDP 9000 (SRT)" in state(services, "config.error")

    # The operator picks a free port: it is written, and the error goes away.
    services.settings.update("feeds", {"srt_port": 9001})
    poll(fake_clock, times=2)
    assert written_config(data_directory)["srtAddress"] == ":9001"
    assert state(services, "config.error") == ""
    assert health(services) == (HealthState.OK, "")


def test_two_quick_changes_both_reach_mediamtx(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    # MediaMTX ignores a second file change within a second of a reload. The
    # module never writes twice within two seconds, so the second change is
    # held back, then written, and MediaMTX ends up with both feeds.
    services.settings.update("feeds", {"feeds": [DRONE]})
    services.settings.update("feeds", {"feeds": [DRONE, LAPTOP]})
    poll(fake_clock, times=3)

    names = [item["name"] for item in state(services, "feeds")]
    assert names == ["drone", "laptop1"]
    assert feeds_fakes.mediamtx.publish("laptop1", "SRT")
    assert state(services, "config.waiting_for_reload") is False


def test_a_reload_mediamtx_missed_is_written_again(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock, data_directory: Path
) -> None:
    # Someone else touches the file (or a reload event was lost): the fake
    # MediaMTX reloads it, and within the same second the studio's change
    # arrives, which MediaMTX's watcher drops (measured behaviour).
    path = config_path(data_directory)
    path.write_text(path.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
    feeds_fakes.mediamtx.look_at_config_file()
    services.settings.update("feeds", {"feeds": [DRONE]})
    # The studio wrote at once (same fake second), so MediaMTX did not take it.
    eventually(lambda: "drone" in written_config(data_directory)["paths"])
    assert not feeds_fakes.mediamtx.publish("drone")

    # A few polls later the module noticed the fingerprint did not match,
    # wrote the file again, and MediaMTX took it.
    poll(fake_clock, times=5)
    assert feeds_fakes.mediamtx.publish("drone")
    assert state(services, "config.waiting_for_reload") is False
    assert feeds_fakes.services.restarts == []


def test_the_password_never_reaches_state_logs_export_or_the_api(
    make_app: AppFactory,
    feeds_fakes: FeedsFakes,
    fake_clock: FakeClock,
    data_directory: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = make_app(lambda registry: [FeedsModule(feeds_fakes.services, feeds_fakes.system())])
    services: StudioServices = app.state.services
    with TestClient(app) as http:
        # A password-protected laptop feed, saved through the settings API (as the page does).
        document = http.get("/api/settings/feeds").json()
        values = {**document["values"], "feeds": [{**LAPTOP, "password": PASSWORD}]}
        saved = http.put(
            "/api/settings/feeds", json={"version": document["version"], "values": values}
        )
        assert saved.status_code == 200
        poll(fake_clock)

        # MediaMTX accepts the laptop only with the password: the hash in the file works.
        mediamtx = feeds_fakes.mediamtx
        assert not mediamtx.publish("laptop1", "SRT", user="laptop1", password="wrong")
        assert mediamtx.publish("laptop1", "SRT", user="laptop1", password=PASSWORD)
        poll(fake_clock)

        # And the password itself is nowhere: not in the save's answer, the
        # settings the page reads back, the state the pages receive, the log,
        # the file MediaMTX reads, or the settings export.
        read_back = http.get("/api/settings/feeds").text
        assert PASSWORD not in saved.text
        assert PASSWORD not in read_back
        assert PASSWORD not in json.dumps(services.state.snapshot())
        assert PASSWORD not in caplog.text
        assert PASSWORD not in config_path(data_directory).read_text(encoding="utf-8")
        exported = export_document(services.settings, fake_clock, "test-pc")
        assert PASSWORD not in json.dumps(exported)
        assert "feeds.feeds[" in " ".join(exported["secrets_required"])
        # The card says there is one, so the page can explain the <password> placeholder.
        laptop = card(services, "laptop1")
        assert laptop["has_password"] is True
        assert laptop["publish_urls"][0]["url"].endswith(
            "streamid=publish:laptop1:laptop1:<password>&pkt_size=1316&latency=200000"
        )


def test_restarting_the_studio_leaves_mediamtx_and_live_feeds_alone(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock, data_directory: Path
) -> None:
    # The studio service restarts (an upgrade, a crash) while the drone is live.
    services.settings.update("feeds", {"feeds": [DRONE]})
    poll(fake_clock)
    assert feeds_fakes.mediamtx.publish("drone")
    file_before = config_path(data_directory).stat().st_mtime_ns
    reloads_before = feeds_fakes.mediamtx.reloads

    services.registry.stop_module("feeds")
    services.registry.start_module("feeds")
    poll(fake_clock, times=2)

    # Same settings, same file: not rewritten, so MediaMTX did not even reload,
    # the drone stayed live, and the feeds service was not restarted.
    assert config_path(data_directory).stat().st_mtime_ns == file_before
    assert feeds_fakes.mediamtx.reloads == reloads_before
    assert card(services, "drone")["status"] == "live"
    assert feeds_fakes.services.restarts == []


def test_add_to_obs_creates_the_feeds_media_source_in_the_chosen_scene(
    make_app: AppFactory, feeds_fakes: FeedsFakes, fake_clock: FakeClock
) -> None:
    # OBS (a fake) with a "Remote" scene, reached the way playout lends its connection.
    obs = FakeObs(fake_clock)
    obs.add_scene("Main")
    obs.add_scene("Remote")
    network = InProcessObsNetwork()
    network.plug("obs", 4455, obs)
    client = ObsWebsocketClient(fake_clock, network.connect)
    client.connect(ObsAddress("obs", 4455), "")
    module = FeedsModule(feeds_fakes.services, feeds_fakes.system(), ConnectedGateway(client))
    app = make_app(lambda registry: [module])
    services: StudioServices = app.state.services
    try:
        with TestClient(app):
            services.settings.update("feeds", {"feeds": [DRONE]})

            def send(command: str, **args: Any) -> Any:
                return services.registry.dispatch_command("feeds", command, args, "web:test")

            # The page fills its scene picker, then the operator clicks Add to OBS.
            assert send("obs_scenes") == {"scenes": ["Main", "Remote"]}
            result = send("add_to_obs", feed="drone", scene="Remote")

            # The source is named after the feed's label and reads the local RTSP URL.
            assert result["source"] == "Drone"
            assert [item.source_name for item in obs.scenes["Remote"]] == ["Drone"]
            assert obs.inputs["Drone"].settings["input"] == "rtsp://127.0.0.1:8554/drone"
            # A second click is refused: the studio never changes an existing source.
            with pytest.raises(CommandError, match="already has a source named 'Drone'"):
                send("add_to_obs", feed="drone", scene="Main")
            with pytest.raises(CommandError, match="no feed named 'nothing'"):
                send("add_to_obs", feed="nothing", scene="Main")
            with pytest.raises(CommandError, match="pick the OBS scene"):
                send("add_to_obs", feed="drone", scene="")
            # Nothing was created in OBS without those clicks.
            assert len(obs.requests_of("CreateInput")) == 1
    finally:
        client.close()


def test_a_refused_password_is_not_echoed_in_the_error(
    make_app: AppFactory, feeds_fakes: FeedsFakes
) -> None:
    # "pass:word" has a ':' (it would end the SRT stream id early), so the save
    # is refused. The error says why, without repeating the password; and a
    # refusal of the whole section (accept-any with a password) does not
    # repeat any password either.
    app = make_app(lambda registry: [FeedsModule(feeds_fakes.services, feeds_fakes.system())])
    with TestClient(app) as http:
        version = http.get("/api/settings/feeds").json()["version"]
        bad = {"feeds": [{**DRONE, "password": "pass:word"}]}
        refused = http.put("/api/settings/feeds", json={"version": version, "values": bad})
        assert refused.status_code == 422
        assert "pass:word" not in refused.text
        assert "4 to 64" in refused.text

        both = {"feeds": [{**DRONE, "password": PASSWORD}], "accept_any_name": True}
        refused = http.put("/api/settings/feeds", json={"version": version, "values": both})
        assert refused.status_code == 422
        assert PASSWORD not in refused.text


def test_a_refused_port_is_tried_again_once_the_other_program_has_gone(
    services: StudioServices, feeds_fakes: FeedsFakes, fake_clock: FakeClock, data_directory: Path
) -> None:
    # The page says "close it, or pick another port": closing the other
    # program must be enough, without saving the settings again.
    feeds_fakes.taken_ports[("TCP", 1940)] = "nginx.exe (pid 99)"
    services.settings.update("feeds", {"rtmp_port": 1940})
    poll(fake_clock)
    assert state(services, "config.error") != ""

    del feeds_fakes.taken_ports[("TCP", 1940)]
    poll(fake_clock, times=16)  # the retry comes within 30 s

    assert written_config(data_directory)["rtmpAddress"] == ":1940"
    assert state(services, "config.error") == ""
    assert feeds_fakes.mediamtx.running
