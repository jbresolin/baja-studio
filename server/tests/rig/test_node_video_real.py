"""The camera node's video pipeline with a real ffmpeg (WP15 brief §3, "Tests on Linux in CI").

A synthetic 4K camera stands in for the webcam: `quadrants` (four flat
greys, so the brightness of what comes out tells where the window is) or
ffmpeg's `testsrc2` test card. The pipeline encodes with x264 and sends
either to a local UDP port, read back by a `PictureProbe` (a second ffmpeg
that shrinks every frame to 32x18 grey pixels), or to the real pinned
MediaMTX over SRT, as on the day.

These run in real time and open loopback sockets, so they are rig tests
(`pytest -m rig`). See `node_video_tools.py` for which ffmpeg they use.
"""

import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest

from baja_studio.core.clock import SystemClock
from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.encoders import AUTO_ORDER
from baja_studio.node.video.ffmpeg_process import (
    BytesSink,
    FfmpegProcess,
    LineSink,
    RunningFfmpeg,
    launch_ffmpeg,
)
from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.pipeline_parts import PipelineParts, ZmqSenders, free_loopback_port
from baja_studio.node.video.probe_reader import PictureProbe, first_changed_frame
from baja_studio.node.video.spec import PipelineSpec, PipelineState, SourceWindow
from baja_studio.rig.layout import free_udp_port
from tests.rig.node_video_tools import ffmpeg_path, needs_node_ffmpeg, wait_until

pytestmark = [pytest.mark.rig, needs_node_ffmpeg]

LOGGER = logging.getLogger("test.node_video")
TOP_LEFT = SourceWindow(0.0, 0.0, 0.5, 0.5)
TOP_RIGHT = SourceWindow(0.5, 0.0, 0.5, 0.5)
BOTTOM_LEFT = SourceWindow(0.0, 0.5, 0.5, 0.5)
BOTTOM_RIGHT = SourceWindow(0.5, 0.5, 0.5, 0.5)
# The grey a probe reads for each quarter of the quadrants picture (0, 85, 170, 255
# drawn; video's limited range and the encoder move them a little).
EXPECTED_GREY = {TOP_LEFT: 0, TOP_RIGHT: 85, BOTTOM_LEFT: 170, BOTTOM_RIGHT: 255}
GREY_TOLERANCE = 25


class RecordingLauncher:
    """The real launcher, keeping every ffmpeg it starts (to kill one, or check they ended)."""

    def __init__(self) -> None:
        self.processes: list[FfmpegProcess] = []

    def __call__(self, command: list[str], on_line: LineSink, on_bytes: BytesSink) -> RunningFfmpeg:
        process = launch_ffmpeg(command, on_line, on_bytes)
        assert isinstance(process, FfmpegProcess)
        self.processes.append(process)
        return process


class Camera:
    """A real pipeline on a synthetic camera, sending to a local UDP port read by a probe."""

    def __init__(self, source: str = "quadrants", width: int = 3840, height: int = 2160) -> None:
        self.port = free_udp_port()
        self.launcher = RecordingLauncher()
        self.spec = PipelineSpec(
            device=f"lavfi:{source}",
            input_width=width,
            input_height=height,
            input_fps=30,
            output_url=f"udp://127.0.0.1:{self.port}?pkt_size=1316",
            encoder="libx264",
            ffmpeg_path=ffmpeg_path(),
            label=f"synthetic {source}",
        )
        self.pipeline = CameraPipeline(
            self.spec, SystemClock(), LOGGER, PipelineParts(launch=self.launcher)
        )
        self.probe: PictureProbe | None = None

    def start(self, with_probe: bool = True) -> "Camera":
        if with_probe:
            self.probe = PictureProbe(ffmpeg_path(), f"udp://127.0.0.1:{self.port}")
        self.pipeline.start()
        wait_until(lambda: self.state() == PipelineState.RUNNING, "the pipeline running")
        if self.probe is not None:
            probe = self.probe
            wait_until(lambda: probe.latest() is not None, "the probe seeing pictures")
        return self

    def state(self) -> PipelineState:
        return self.pipeline.status().state

    def grey(self) -> float:
        assert self.probe is not None
        latest = self.probe.latest()
        assert latest is not None
        return latest.mean()

    def shows(self, window: SourceWindow) -> bool:
        return abs(self.grey() - EXPECTED_GREY[window]) <= GREY_TOLERANCE

    def close(self) -> None:
        self.pipeline.stop()
        if self.probe is not None:
            self.probe.close()


@pytest.fixture
def cameras() -> Iterator[list[Camera]]:
    """Cameras a test makes; all stopped afterwards, even if the test fails."""
    made: list[Camera] = []
    yield made
    for camera in made:
        camera.close()


def make(
    cameras: list[Camera], source: str = "quadrants", width: int = 3840, height: int = 2160
) -> Camera:
    camera = Camera(source, width, height)
    cameras.append(camera)
    return camera


def showing(camera: Camera, window: SourceWindow) -> Callable[[], bool]:
    """A condition for `wait_until`: the probe sees `window`'s grey."""
    return lambda: camera.shows(window)


def kill(process: FfmpegProcess) -> None:
    """Kill ffmpeg as a crash would (no chance to clean up)."""
    os.kill(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def test_window_commands_move_the_picture_live(cameras: list[Camera]) -> None:
    # The pipeline starts on the whole frame: the probe sees the average of
    # the four greys (about 128).
    camera = make(cameras).start()
    wait_until(lambda: abs(camera.grey() - 128) < GREY_TOLERANCE, "the whole frame")
    # Each quarter in turn, at 2x: the probe sees that quarter's grey.
    for window in (TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT, TOP_LEFT):
        camera.pipeline.set_window(window)
        wait_until(showing(camera, window), f"the picture at {window}")
    # Same ffmpeg throughout: the window moved without a restart.
    assert len(camera.launcher.processes) == 1
    assert camera.pipeline.status().restarts == 0


def test_the_output_stays_1920x1080_whatever_the_zoom(cameras: list[Camera]) -> None:
    camera = make(cameras).start(with_probe=False)
    # Zoom in and out through many sizes, including 2x (exactly 1920 wide).
    for width in (1.0, 0.8, 0.5, 0.66, 0.9, 0.5):
        camera.pipeline.set_window(SourceWindow(0.1, 0.1, width, width))
        time.sleep(0.3)
    # Another ffmpeg decodes one frame of the stream as it is now, at its own
    # size, in grey: one byte per pixel, so 1920 x 1080 bytes. (A size change
    # mid-stream would also have stopped x264, and the pipeline would have
    # restarted.)
    one_frame = subprocess.run(
        [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error",
            "-i", f"udp://127.0.0.1:{camera.port}",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        capture_output=True, timeout=30, check=True,
    )  # fmt: skip
    assert len(one_frame.stdout) == 1920 * 1080
    assert camera.pipeline.status().restarts == 0


def test_thirty_window_updates_a_second_for_ten_seconds_never_stall(
    cameras: list[Camera],
) -> None:
    camera = make(cameras, source="testsrc2").start()
    # The rate this machine manages with a still window (CI machines differ;
    # the bench measures the laptops).
    time.sleep(3)
    still = camera.pipeline.status()
    # A joystick held for ten seconds: 300 updates, panning and zooming.
    for step in range(300):
        phase = step / 30
        width = 0.75 + 0.25 * ((step % 60) / 60)
        camera.pipeline.set_window(SourceWindow((1 - width) * (phase % 1), 0.1, width, width))
        time.sleep(1 / 30)
    status = camera.pipeline.status()
    assert status.state == PipelineState.RUNNING
    assert status.restarts == 0
    # Pictures kept coming, about as fast as with a still window...
    assert status.fps_out >= 0.8 * still.fps_out
    probe = camera.probe
    assert probe is not None
    latest = probe.latest()
    assert latest is not None and time.monotonic() - latest.read_at < 1.0
    # ...and window changes kept reaching frames quickly.
    assert status.window_apply_ms is not None and status.window_apply_ms < 200


def test_snapshots_come_about_once_a_second_and_show_the_whole_frame(
    cameras: list[Camera],
) -> None:
    camera = make(cameras).start(with_probe=False)
    # The live window is the top-left quarter only...
    camera.pipeline.set_window(TOP_LEFT)
    seen: list[bytes] = []
    deadline = time.monotonic() + 5.5
    while time.monotonic() < deadline:
        snapshot = camera.pipeline.latest_snapshot()
        if snapshot is not None and (not seen or snapshot is not seen[-1]):
            seen.append(snapshot)
        time.sleep(0.05)
    # About one a second.
    assert 3 <= len(seen) <= 8
    # ...but the snapshot is the whole 4K frame, 320x180: all four greys are in it.
    pixels = decode_grey(seen[-1], 320, 180)
    corners = {
        "top_left": pixels[45 * 320 + 80],
        "top_right": pixels[45 * 320 + 240],
        "bottom_left": pixels[135 * 320 + 80],
        "bottom_right": pixels[135 * 320 + 240],
    }
    assert corners["top_left"] < 40
    assert 60 < corners["top_right"] < 110
    assert 145 < corners["bottom_left"] < 195
    assert corners["bottom_right"] > 215


def test_a_killed_ffmpeg_is_restarted_on_the_same_window(cameras: list[Camera]) -> None:
    camera = make(cameras).start()
    camera.pipeline.set_window(BOTTOM_RIGHT)
    wait_until(lambda: camera.shows(BOTTOM_RIGHT), "the bottom-right quarter")
    first = camera.launcher.processes[0]
    kill(first)
    wait_until(lambda: camera.state() != PipelineState.RUNNING, "the pipeline noticing")
    # One second later a new ffmpeg runs, starting where the window was.
    wait_until(lambda: camera.state() == PipelineState.RUNNING, "running again")
    assert len(camera.launcher.processes) == 2
    assert camera.pipeline.status().restarts == 1
    wait_until(lambda: camera.shows(BOTTOM_RIGHT), "the same quarter after the restart")
    # The killed one was reaped.
    assert first.exited.is_set() and first.exit_code is not None


def test_stopping_leaves_no_ffmpeg_running(cameras: list[Camera]) -> None:
    camera = make(cameras).start()
    camera.pipeline.stop()
    assert camera.state() == PipelineState.STOPPED
    for process in camera.launcher.processes:
        assert process.exited.is_set() and process.exit_code is not None


def test_two_cameras_are_independent(cameras: list[Camera]) -> None:
    # Two pipelines (smaller pictures, to spare the CI machine).
    left = make(cameras, width=1280, height=720).start(with_probe=False)
    right = make(cameras, width=1280, height=720).start(with_probe=False)
    kill(left.launcher.processes[0])
    wait_until(lambda: left.pipeline.status().restarts == 1, "the left camera restarting")
    wait_until(lambda: left.state() == PipelineState.RUNNING, "the left camera running again")
    # The right one never noticed.
    assert right.state() == PipelineState.RUNNING
    assert right.pipeline.status().restarts == 0
    assert len(right.launcher.processes) == 1


def test_auto_picks_an_encoder_that_works(cameras: list[Camera]) -> None:
    camera = make(cameras, width=1280, height=720)
    camera.pipeline = CameraPipeline(
        replace(camera.spec, encoder="auto"), SystemClock(), LOGGER, PipelineParts()
    )
    camera.start(with_probe=False)
    # On CI that is x264 (no GPU); on a laptop, its GPU encoder.
    assert camera.pipeline.status().encoder in AUTO_ORDER


def test_window_move_latency_is_measured(
    cameras: list[Camera], record_property: Callable[[str, object], None]
) -> None:
    # The number the brief asks for: from set_window to the first frame that
    # shows it, as read back by a probe (encode and decode included, no network).
    camera = make(cameras).start()
    camera.pipeline.set_window(TOP_LEFT)
    wait_until(lambda: camera.shows(TOP_LEFT), "the top-left quarter")
    probe = camera.probe
    assert probe is not None
    latencies = []
    for move in range(10):
        target = BOTTOM_RIGHT if move % 2 == 0 else TOP_LEFT
        time.sleep(0.5)
        reference = probe.latest()
        assert reference is not None
        sent_at = time.monotonic()
        camera.pipeline.set_window(target)
        wait_until(showing(camera, target), f"move {move}")
        changed = first_changed_frame(probe.frames_after(sent_at), reference, 20.0)
        assert changed is not None
        latencies.append((changed.read_at - sent_at) * 1000)
    latencies.sort()
    median = latencies[len(latencies) // 2]
    apply_ms = camera.pipeline.status().window_apply_ms
    record_property("move_latency_median_ms", round(median))
    record_property("window_apply_median_ms", apply_ms)
    print(f"move latency: median {median:.0f} ms, max {latencies[-1]:.0f} ms; apply {apply_ms} ms")
    assert median < 500


def test_real_zmq_senders_reach_ffmpeg_on_a_free_port() -> None:
    # The real sender factory and a real port, without a pipeline: a sender to
    # a port nobody listens on starts and stops, and the context closes after.
    senders = ZmqSenders(SystemClock())
    sender = senders(free_loopback_port(), CropPixels(3840, 1920, 1080), LOGGER)
    sender.start()
    sender.stop()
    senders.close()


def decode_grey(jpeg: bytes, width: int, height: int) -> bytes:
    """Decode a JPEG with ffmpeg to raw grey pixels (and check its size on the way)."""
    decoded = subprocess.run(
        [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        input=jpeg, capture_output=True, timeout=30, check=True,
    )  # fmt: skip
    assert len(decoded.stdout) == width * height
    return decoded.stdout
