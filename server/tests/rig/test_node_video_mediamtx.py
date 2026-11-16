"""The camera node's pipeline sending to the real MediaMTX over SRT, as on the day (WP15 + WP14).

MediaMTX runs with the config the studio writes (a laptop feed with a
password), on free loopback ports. The pipeline sends a synthetic 4K camera
to the SRT URL the Feeds page shows, with the password filled in. These need
the pinned MediaMTX (CI downloads the Linux build; `BAJA_STUDIO_TEST_MEDIAMTX`)
and the node's ffmpeg (`node_video_tools.py`), and are skipped without them.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from baja_studio.core.clock import SystemClock
from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxApiClient
from baja_studio.modules.feeds.mediamtx_config import config_path, render_config, write_config
from baja_studio.modules.feeds.urls import srt_url
from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.spec import PipelineSpec, PipelineState
from baja_studio.rig.layout import LOOPBACK
from tests.rig.node_video_tools import ffmpeg_path, needs_node_ffmpeg, wait_until
from tests.rig.test_mediamtx_real import MEDIAMTX, RunningMediamtx, free_ports

pytestmark = [
    pytest.mark.rig,
    needs_node_ffmpeg,
    pytest.mark.skipif(MEDIAMTX is None, reason="no MediaMTX binary to run"),
]

PASSWORD = "node-pw-123"
SALT = bytes(range(16))
LOGGER = logging.getLogger("test.node_video_mediamtx")


class Studio:
    """MediaMTX configured with one laptop feed, which can be started and stopped."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.settings: FeedsSettings = free_ports().model_copy(
            update={"feeds": [Feed(name="laptop1", kind=FeedKind.LAPTOP, password=PASSWORD)]}
        )
        self.path = config_path(folder)
        write_config(self.path, render_config(self.settings, folder, SALT))
        self.client = MediamtxApiClient(f"{LOOPBACK}:{self.settings.api_port}")
        self.running: RunningMediamtx | None = None

    @property
    def sender_url(self) -> str:
        """The URL the Feeds page shows for the laptop, password typed in."""
        url = srt_url(self.settings.feeds[0], self.settings, LOOPBACK)
        return url.replace("<password>", PASSWORD)

    def start(self) -> None:
        self.running = RunningMediamtx(self.path)

    def stop(self) -> None:
        if self.running is not None:
            self.running.stop()
            self.running = None

    def live_video(self) -> tuple[int, int] | None:
        """The laptop feed's picture size while it is live, else None."""
        try:
            for path in self.client.paths():
                video = path.video
                if (
                    path.name == "laptop1"
                    and path.online
                    and video is not None
                    and video.width is not None
                    and video.height is not None
                ):
                    return video.width, video.height
        except Exception:  # MediaMTX still starting: not live yet
            return None
        return None


@pytest.fixture
def studio(tmp_path: Path) -> Iterator[Studio]:
    studio = Studio(tmp_path)
    yield studio
    studio.stop()


def make_pipeline(url: str) -> CameraPipeline:
    spec = PipelineSpec(
        device="lavfi:testsrc2",
        input_width=3840,
        input_height=2160,
        input_fps=30,
        output_url=url,
        encoder="libx264",
        ffmpeg_path=ffmpeg_path(),
    )
    return CameraPipeline(spec, SystemClock(), LOGGER)


def test_the_feed_goes_live_on_mediamtx_at_1920x1080(studio: Studio) -> None:
    studio.start()
    pipeline = make_pipeline(studio.sender_url)
    try:
        pipeline.start()
        wait_until(lambda: studio.live_video() is not None, "the laptop feed live")
        # MediaMTX sees the size the node sends: 1080p, not the camera's 4K.
        assert studio.live_video() == (1920, 1080)
        wait_until(lambda: pipeline.status().state == PipelineState.RUNNING, "the node running")
        # The password is in the URL ffmpeg uses, but never in what the node shows.
        assert not any(PASSWORD in line for line in pipeline.log_tail())
    finally:
        pipeline.stop()


def test_the_receiver_away_then_back(studio: Studio) -> None:
    # The node starts before the studio's MediaMTX: it cannot connect, and says so.
    pipeline = make_pipeline(studio.sender_url)
    try:
        pipeline.start()
        wait_until(
            lambda: pipeline.status().state == PipelineState.RESTARTING, "a first failed attempt"
        )
        reason = pipeline.status().reason
        assert reason.startswith(f"SRT receiver unreachable: 127.0.0.1:{studio.settings.srt_port}")
        assert PASSWORD not in reason
        # MediaMTX comes up: within the next attempts the feed goes live.
        studio.start()
        wait_until(lambda: studio.live_video() is not None, "the feed live once MediaMTX is up")
        wait_until(lambda: pipeline.status().state == PipelineState.RUNNING, "running")
        # MediaMTX goes away (a studio PC restart): the node notices...
        studio.stop()
        wait_until(lambda: pipeline.status().state != PipelineState.RUNNING, "the loss noticed")
        assert pipeline.status().reason.startswith(
            f"SRT receiver lost: 127.0.0.1:{studio.settings.srt_port}"
        )
        # ...and is live again soon after MediaMTX is back.
        studio.start()
        wait_until(lambda: studio.live_video() is not None, "live again")
        assert pipeline.status().restarts >= 2
    finally:
        pipeline.stop()
