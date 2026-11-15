"""Tests for the MediaMTX control API client (modules/feeds/control_api.py).

The answers here are not made up: `feeds_fixtures/*.json` were recorded
from the real MediaMTX v1.21.3 (Linux build) with ffmpeg publishing test
pictures, in November 2026:

- `paths_list_waiting.json`: two configured feeds, nobody publishing;
- `paths_list_live.json`: the drone live over RTMP (1280x720) with OBS
  (an RTSP session) reading it, laptop1 live over SRT (1920x1080);
- `paths_list_accept_any.json`: a sender on an unlisted name, accepted by
  the catch-all path (`confName` all_others);
- `info.json`, `config_global_get.json`: version and the running config
  (the password comes back as "<redacted>").

Each test serves one recording through a mock transport (no network).
"""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from baja_studio.modules.feeds.control_api import (
    MediamtxApiClient,
    MediamtxUnreachableError,
    VideoTrack,
)

FIXTURES = Path(__file__).parent / "feeds_fixtures"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def client_answering(routes: dict[str, Any], status: int = 200) -> MediamtxApiClient:
    """A client whose MediaMTX answers each path with the given JSON."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx2.Response(404, json={"status": "error", "error": "not found"})
        return httpx2.Response(status, json=body)

    return MediamtxApiClient("127.0.0.1:9997", httpx2.MockTransport(handle))


def test_info_says_which_mediamtx_answers() -> None:
    info = client_answering({"/v3/info": recorded("info.json")}).info()
    assert info.version == "v1.21.3"
    assert info.started is not None and info.started.startswith("2026-11-15T")


def test_configured_feeds_with_nobody_publishing_are_waiting() -> None:
    paths = client_answering({"/v3/paths/list": recorded("paths_list_waiting.json")}).paths()
    assert [path.name for path in paths] == ["drone", "laptop1"]
    assert all(not path.online for path in paths)
    # Nothing is reported for a feed nobody sends: no protocol, no picture.
    assert paths[0].protocol is None and paths[0].video is None
    assert paths[0].inbound_bytes == 0


def test_live_feeds_report_protocol_picture_sound_readers_and_bytes() -> None:
    drone, laptop = client_answering({"/v3/paths/list": recorded("paths_list_live.json")}).paths()
    assert drone.online is True
    assert drone.protocol == "RTMP"  # MediaMTX says "rtmpConn"
    assert drone.video == VideoTrack("H264", 1280, 720)
    assert drone.audio_codec == "MPEG-4 Audio"  # AAC, in MediaMTX's words
    assert drone.readers == 1  # OBS, reading over RTSP
    assert drone.inbound_bytes == 2581927
    assert drone.online_since == "2026-11-15T19:43:32.841286093-03:00"
    assert laptop.protocol == "SRT"
    assert laptop.video == VideoTrack("H264", 1920, 1080)
    assert laptop.readers == 0


def test_a_sender_accepted_by_the_catch_all_keeps_its_own_name() -> None:
    paths = client_answering({"/v3/paths/list": recorded("paths_list_accept_any.json")}).paths()
    other = next(path for path in paths if path.name == "whatever")
    assert other.conf_name == "all_others"
    assert other.online is True


def test_the_running_config_comes_back_with_the_password_redacted() -> None:
    config = client_answering(
        {"/v3/config/global/get": recorded("config_global_get.json")}
    ).global_config()
    assert config["apiAddress"] == "127.0.0.1:19997"
    assert config["authInternalUsers"][0]["pass"] == "<redacted>"


def test_no_answer_means_mediamtx_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = MediamtxApiClient("127.0.0.1:9997", httpx2.MockTransport(refuse))
    expected = r"no answer from MediaMTX at 127\.0\.0\.1:9997"
    with pytest.raises(MediamtxUnreachableError, match=expected):
        client.info()


def test_an_error_status_or_a_page_that_is_not_json_is_unreachable_too() -> None:
    # Something else answering on the API port (a web server) must not look like MediaMTX.
    with pytest.raises(MediamtxUnreachableError, match="HTTP 404"):
        client_answering({}).info()

    def html(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>hello</html>")

    with pytest.raises(MediamtxUnreachableError, match="not JSON"):
        MediamtxApiClient("127.0.0.1:9997", httpx2.MockTransport(html)).info()
