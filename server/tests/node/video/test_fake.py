"""Tests for `FakeCameraPipeline`, the stand-in the node's tests and the rig use (fake.py).

The node's tests rely on this fake behaving like the real pipeline, so its
behaviour is pinned down here.
"""

import logging
from dataclasses import replace

from baja_studio.node.video.fake import FakeCameraPipeline, fake_snapshot_jpeg
from baja_studio.node.video.spec import PipelineSpec, PipelineState, SourceWindow
from tests.conftest import FakeClock

SPEC = PipelineSpec(
    device="EMEET SmartCam S600",
    input_width=3840,
    input_height=2160,
    input_fps=30,
    output_url="srt://studio:8890?streamid=publish:laptop1",
)


def make(clock: FakeClock, encoder: str = "auto") -> FakeCameraPipeline:
    return FakeCameraPipeline(replace(SPEC, encoder=encoder), clock, logging.getLogger("test.fake"))


def test_it_is_stopped_until_started_then_running_at_the_camera_rate(fake_clock: FakeClock) -> None:
    fake = make(fake_clock)
    assert fake.status().state == PipelineState.STOPPED
    fake.start()
    status = fake.status()
    assert status.state == PipelineState.RUNNING
    assert (status.fps_in, status.fps_out, status.bitrate_kbps) == (30, 30, 8000)
    # "auto" pretends x264 was chosen; a named encoder is reported as is.
    assert status.encoder == "libx264"


def test_a_named_encoder_is_reported(fake_clock: FakeClock) -> None:
    fake = make(fake_clock, encoder="h264_qsv")
    fake.start()
    assert fake.status().encoder == "h264_qsv"


def test_windows_are_recorded_in_order_and_survive_a_restart(fake_clock: FakeClock) -> None:
    fake = make(fake_clock)
    fake.start()
    first, second = SourceWindow(0, 0, 0.5, 0.5), SourceWindow(0.5, 0.5, 0.5, 0.5)
    fake.set_window(first)
    fake.set_window(second)
    assert fake.windows == [first, second]
    fake.stop()
    fake.start()
    assert fake.window == second
    assert (fake.starts, fake.stops) == (2, 1)


def test_a_real_jpeg_is_served_only_while_running(fake_clock: FakeClock) -> None:
    fake = make(fake_clock)
    assert fake.latest_snapshot() is None
    fake.start()
    picture = fake.latest_snapshot()
    # A real JPEG: it starts with the JPEG marker FF D8 and ends with FF D9.
    assert picture == fake_snapshot_jpeg()
    assert picture is not None and picture[:2] == b"\xff\xd8" and picture[-2:] == b"\xff\xd9"
    fake.set_snapshot(b"\xff\xd8other\xff\xd9")
    assert fake.latest_snapshot() == b"\xff\xd8other\xff\xd9"


def test_a_scripted_failure_is_reported_until_the_next_start(fake_clock: FakeClock) -> None:
    fake = make(fake_clock)
    fake.start()
    fake.script_status(PipelineState.RESTARTING, "camera not found: S600", restarts=2)
    status = fake.status()
    assert (status.state, status.reason, status.restarts) == (
        PipelineState.RESTARTING,
        "camera not found: S600",
        2,
    )
    # No picture while not running, like the real pipeline.
    assert fake.latest_snapshot() is None
    fake.stop()
    fake.start()
    assert fake.status().state == PipelineState.RUNNING


def test_log_lines_can_be_added(fake_clock: FakeClock) -> None:
    fake = make(fake_clock)
    fake.start()
    fake.add_log_line("[dshow @ 0x1] Could not run graph")
    assert fake.log_tail() == ["--- ffmpeg starting (fake)", "[dshow @ 0x1] Could not run graph"]
