"""The real MediaMTX, with the config the studio writes, and real senders (ffmpeg).

Everything else in the feeds tests runs against the fake MediaMTX. These
tests check that the fake is not lying: they start the pinned MediaMTX
binary on loopback ports, push test pictures with ffmpeg over RTMP and SRT,
and read them back over RTSP as OBS would.

They need the MediaMTX binary (the path in `BAJA_STUDIO_TEST_MEDIAMTX`, or
`mediamtx` on PATH; CI downloads the pinned Linux build, see
installer/mediamtx/pin.json) and ffmpeg with SRT. Without them they are
skipped. Like the other rig tests they open loopback sockets and run in real
time, waiting for each event with a deadline.
"""

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxApiClient, MediamtxUnreachableError
from baja_studio.modules.feeds.mediamtx_config import (
    FINGERPRINT_FIELD,
    config_path,
    parse_config,
    render_config,
    write_config,
)
from baja_studio.modules.feeds.urls import obs_browser_url, obs_read_url, rtmp_url, srt_url
from baja_studio.rig.layout import LOOPBACK, free_tcp_port, free_udp_port

pytestmark = pytest.mark.rig

MEDIAMTX_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_TEST_MEDIAMTX"
GOLDEN_DIRECTORY = Path(__file__).parents[1] / "modules" / "feeds_golden"
SALT = bytes(range(16))
PASSWORD = "laptop1-pw"
DEADLINE_SECONDS = 20.0


def mediamtx_binary() -> str | None:
    return os.environ.get(MEDIAMTX_ENVIRONMENT_VARIABLE) or shutil.which("mediamtx")


def ffmpeg_with_srt() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    protocols = subprocess.run(
        [ffmpeg, "-hide_banner", "-protocols"], capture_output=True, text=True, check=False
    ).stdout
    # `-protocols` lists one name per line, inputs then outputs.
    return ffmpeg if "srt" in protocols.split() and shutil.which("ffprobe") else None


MEDIAMTX = mediamtx_binary()
FFMPEG = ffmpeg_with_srt()
needs_mediamtx = pytest.mark.skipif(MEDIAMTX is None, reason="no MediaMTX binary to run")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg with SRT, or no ffprobe")


def eventually_real(condition: Callable[[], bool], what: str) -> None:
    """Wait (real time) until `condition()` holds, or fail naming `what`."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"never happened: {what}")
        time.sleep(0.2)


class RunningMediamtx:
    """One MediaMTX process reading `config_file`, its output in a log file."""

    def __init__(self, config_file: Path, environment: dict[str, str] | None = None) -> None:
        assert MEDIAMTX is not None
        self.log_path = config_file.with_name("mediamtx.log")
        self._log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [MEDIAMTX, str(config_file)],
            cwd=config_file.parent,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(environment or {})},
        )

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self._log.close()

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")


def free_ports() -> FeedsSettings:
    """Feeds settings on ports nobody uses here (CI machines may have 1935 taken)."""
    return FeedsSettings(
        rtmp_port=free_tcp_port(),
        srt_port=free_udp_port(),
        rtsp_port=free_tcp_port(),
        api_port=free_tcp_port(),
        webrtc_port=free_tcp_port(),
        webrtc_media_port=free_udp_port(),
    )


def push(url: str, container: str, seconds: int = 30) -> subprocess.Popen[bytes]:
    """ffmpeg sending a 1280x720 test picture and a tone to `url`, in real time."""
    assert FFMPEG is not None
    return subprocess.Popen(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-re",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
            "-c:a", "aac", "-f", container, url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip


@pytest.fixture
def senders() -> Iterator[list[subprocess.Popen[bytes]]]:
    """ffmpeg processes started by a test, stopped after it."""
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for process in started:
        process.kill()
        process.wait(timeout=10)


@needs_mediamtx
@pytest.mark.parametrize("golden", sorted(path.name for path in GOLDEN_DIRECTORY.glob("*.yml")))
def test_mediamtx_loads_every_golden_config(golden: str, tmp_path: Path) -> None:
    # The golden files are exactly what the studio writes. MediaMTX's own
    # environment variables move the ports to free ones (CI may use 1935);
    # everything else is read from the file as it is.
    path = tmp_path / "mediamtx.yml"
    path.write_text((GOLDEN_DIRECTORY / golden).read_text(encoding="utf-8"), encoding="utf-8")
    api_port = free_tcp_port()
    webrtc_port = free_tcp_port()
    running = RunningMediamtx(
        path,
        {
            "MTX_APIADDRESS": f"{LOOPBACK}:{api_port}",
            "MTX_RTMPADDRESS": f":{free_tcp_port()}",
            "MTX_SRTADDRESS": f":{free_udp_port()}",
            "MTX_RTSPADDRESS": f"{LOOPBACK}:{free_tcp_port()}",
            "MTX_WEBRTCADDRESS": f"{LOOPBACK}:{webrtc_port}",
            "MTX_WEBRTCLOCALUDPADDRESS": f":{free_udp_port()}",
        },
    )
    try:
        client = MediamtxApiClient(f"{LOOPBACK}:{api_port}")
        eventually_real(lambda: _answers(client), f"MediaMTX answering with {golden}")
        # It runs this very file: the fingerprint it reports is the file's.
        expected = parse_config(path.read_text(encoding="utf-8"))[FINGERPRINT_FIELD]
        assert client.global_config()[FINGERPRINT_FIELD] == expected
        assert client.info().version.startswith("v1.")
    finally:
        running.stop()
    # Only RTSP (TCP), RTMP, SRT, WebRTC and the API were opened: no HLS on 8888.
    assert "[HLS]" not in running.log()
    # WebRTC's page and signalling listen on 127.0.0.1 only.
    listening = f"[WebRTC] started with listeners on {LOOPBACK}:{webrtc_port} (TCP/HTTP)"
    assert listening in running.log()


@needs_mediamtx
@needs_ffmpeg
def test_drone_over_rtmp_and_laptop_over_srt_go_live_and_obs_can_read_them(
    tmp_path: Path, senders: list[subprocess.Popen[bytes]]
) -> None:
    settings = free_ports().model_copy(
        update={
            "feeds": [
                Feed(name="drone", kind=FeedKind.DRONE),
                Feed(name="laptop1", kind=FeedKind.LAPTOP, password=PASSWORD),
            ]
        }
    )
    path = config_path(tmp_path)
    write_config(path, render_config(settings, tmp_path, SALT))
    running = RunningMediamtx(path)
    client = MediamtxApiClient(f"{LOOPBACK}:{settings.api_port}")
    try:
        eventually_real(lambda: _answers(client), "MediaMTX answering")
        drone, laptop = settings.feeds
        # The URLs the Feeds page shows, with the password typed in by the operator.
        senders.append(push(rtmp_url(drone, settings, LOOPBACK), "flv"))
        laptop_url = srt_url(laptop, settings, LOOPBACK).replace("<password>", PASSWORD)
        senders.append(push(laptop_url, "mpegts"))
        # A laptop with the wrong password is turned away.
        senders.append(push(laptop_url.replace(PASSWORD, "wrong-pw"), "mpegts", seconds=3))
        # And a name that is not a feed.
        senders.append(push(rtmp_url(Feed(name="dorne"), settings, LOOPBACK), "flv", 3))

        eventually_real(lambda: _live(client) == {"drone", "laptop1"}, "both feeds live")
        by_name = {path.name: path for path in client.paths()}
        assert by_name["drone"].protocol == "RTMP"
        assert by_name["laptop1"].protocol == "SRT"
        assert by_name["drone"].video is not None and by_name["drone"].video.width == 1280

        # OBS's way in: RTSP over TCP from 127.0.0.1. ffprobe stands in for OBS.
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                "-show_entries", "stream=codec_name,width", "-of", "csv=p=0",
                obs_read_url("drone", settings),
            ],
            capture_output=True, text=True, timeout=DEADLINE_SECONDS, check=False,
        )  # fmt: skip
        assert "h264,1280" in probe.stdout

        eventually_real(lambda: "authentication failed" in running.log(), "wrong password refused")
        eventually_real(
            lambda: "'dorne' is not configured" in running.log(), "unknown name refused"
        )
    finally:
        running.stop()


@needs_mediamtx
@needs_ffmpeg
def test_a_reload_adds_a_feed_without_dropping_a_live_one(
    tmp_path: Path, senders: list[subprocess.Popen[bytes]]
) -> None:
    settings = free_ports().model_copy(update={"feeds": [Feed(name="drone")]})
    path = config_path(tmp_path)
    write_config(path, render_config(settings, tmp_path, SALT))
    running = RunningMediamtx(path)
    client = MediamtxApiClient(f"{LOOPBACK}:{settings.api_port}")
    try:
        eventually_real(lambda: _answers(client), "MediaMTX answering")
        senders.append(push(rtmp_url(settings.feeds[0], settings, LOOPBACK), "flv"))
        eventually_real(lambda: _live(client) == {"drone"}, "drone live")
        live_since = {path.name: path.online_since for path in client.paths()}["drone"]

        # The operator adds laptop1: the studio writes the file, MediaMTX reloads it.
        more = settings.model_copy(update={"feeds": [*settings.feeds, Feed(name="laptop1")]})
        text = render_config(more, tmp_path, SALT)
        write_config(path, text)
        fingerprint = parse_config(text)[FINGERPRINT_FIELD]
        eventually_real(lambda: client.global_config()[FINGERPRINT_FIELD] == fingerprint, "reload")
        assert "laptop1" in {path.name for path in client.paths()}
        # The drone never dropped: same publisher, same "online since".
        assert {path.name: path.online_since for path in client.paths()}["drone"] == live_since
    finally:
        running.stop()


@needs_mediamtx
def test_a_busy_port_on_reload_stops_mediamtx(tmp_path: Path) -> None:
    # Why the studio checks a new port before writing it: MediaMTX exits.
    settings = free_ports()
    path = config_path(tmp_path)
    write_config(path, render_config(settings, tmp_path, SALT))
    running = RunningMediamtx(path)
    client = MediamtxApiClient(f"{LOOPBACK}:{settings.api_port}")
    try:
        eventually_real(lambda: _answers(client), "MediaMTX answering")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
            squatter.bind(("0.0.0.0", 0))
            squatter.listen()
            busy = squatter.getsockname()[1]
            write_config(
                path, render_config(settings.model_copy(update={"rtmp_port": busy}), tmp_path, SALT)
            )
            eventually_real(lambda: running.process.poll() is not None, "MediaMTX exiting")
        assert "address already in use" in running.log()
    finally:
        running.stop()


def _answers(client: MediamtxApiClient) -> bool:
    try:
        client.info()
    except MediamtxUnreachableError:
        return False
    return True


def _live(client: MediamtxApiClient) -> set[str]:
    return {path.name for path in client.paths() if path.online}


@needs_mediamtx
@needs_ffmpeg
def test_a_feed_can_be_read_over_webrtc_on_this_pc(
    tmp_path: Path, senders: list[subprocess.Popen[bytes]]
) -> None:
    # The lowest-delay way into OBS: a Browser Source playing MediaMTX's
    # WebRTC player page. Here a second MediaMTX stands in for the browser:
    # it reads the feed over WebRTC (WHEP, the same request the page makes)
    # on loopback, with no STUN server, and ffprobe reads it back from that
    # second MediaMTX. If it gets the picture, a WebRTC read on this PC works
    # with the config the studio writes.
    settings = free_ports().model_copy(
        update={"feeds": [Feed(name="laptop1", kind=FeedKind.LAPTOP)]}
    )
    path = config_path(tmp_path)
    write_config(path, render_config(settings, tmp_path, SALT))
    studio = RunningMediamtx(path)
    reader_folder = tmp_path / "reader"
    reader_folder.mkdir()
    reader_rtsp_port = free_tcp_port()
    reader_config = reader_folder / "mediamtx.yml"
    # JSON is YAML: the second MediaMTX only reads the feed over WHEP and serves RTSP.
    reader_config.write_text(
        json.dumps(
            {
                "api": False, "hls": False, "webrtc": False, "srt": False, "rtmp": False,
                "moq": False, "playback": False, "metrics": False, "pprof": False,
                "rtspTransports": ["tcp"],
                "rtspAddress": f"{LOOPBACK}:{reader_rtsp_port}",
                "paths": {
                    "copy": {
                        "source": f"whep://{LOOPBACK}:{settings.webrtc_port}/laptop1/whep",
                    }
                },
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    reader: RunningMediamtx | None = None
    client = MediamtxApiClient(f"{LOOPBACK}:{settings.api_port}")
    try:
        eventually_real(lambda: _answers(client), "MediaMTX answering")
        senders.append(push(srt_url(settings.feeds[0], settings, LOOPBACK), "mpegts"))
        eventually_real(lambda: _live(client) == {"laptop1"}, "the laptop feed live")

        # The page a Browser Source opens is served, from 127.0.0.1 only.
        page = httpx2.get(obs_browser_url("laptop1", settings), timeout=DEADLINE_SECONDS)
        assert page.status_code == 200
        assert "reader.js" in page.text

        reader = RunningMediamtx(reader_config)

        def read_back() -> str:
            return subprocess.run(
                [
                    "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                    "-show_entries", "stream=codec_name,width", "-of", "csv=p=0",
                    f"rtsp://{LOOPBACK}:{reader_rtsp_port}/copy",
                ],
                capture_output=True, text=True, timeout=DEADLINE_SECONDS, check=False,
            ).stdout  # fmt: skip

        eventually_real(lambda: "h264,1280" in read_back(), "the picture read back over WebRTC")
        # The studio's MediaMTX served it to a WebRTC reader.
        assert "[WebRTC] [session" in studio.log()
    finally:
        if reader is not None:
            reader.stop()
        studio.stop()
