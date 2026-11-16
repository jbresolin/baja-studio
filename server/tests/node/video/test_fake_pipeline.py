"""Tests for the fake video pipeline (node/video/fake_pipeline.py).

The fake stands in for the real ffmpeg pipeline (Part B) in the node's tests
and in the rehearsal rig. These tests pin down what the node relies on: it
records windows, reports a scripted state, keeps its window through a
failure, and serves a whole-frame JPEG while running.
"""

import logging

from baja_studio.fakes.blocky_jpeg import jpeg_size
from baja_studio.node.test_cameras import fake_output_url
from baja_studio.node.video.fake_pipeline import (
    FAKE_DEVICES,
    FakeCameraPipeline,
    FakeCaptureDevices,
    FakePipelines,
)
from baja_studio.node.video.pipeline_types import PipelineSpec, PipelineState, PipelineWindow
from tests.conftest import FakeClock

LOGGER = logging.getLogger("tests.fake_pipeline")


def spec(encoder: str = "auto") -> PipelineSpec:
    return PipelineSpec(
        device=FAKE_DEVICES[0].device,
        input_width=3840,
        input_height=2160,
        input_fps=30,
        output_url=fake_output_url("cam1"),
        encoder=encoder,
    )


def test_the_output_url_is_kept_out_of_the_specs_repr() -> None:
    # A spec that ends up in a log line must not carry the feed's password.
    assert "srt://" not in repr(spec())


def test_it_starts_running_and_reports_the_specs_frame_rate(fake_clock: FakeClock) -> None:
    pipeline = FakeCameraPipeline(spec(), fake_clock, LOGGER)
    assert pipeline.status().state == PipelineState.STOPPED

    pipeline.start()

    status = pipeline.status()
    assert status.state == PipelineState.RUNNING
    assert (status.input_fps, status.output_fps) == (30.0, 30.0)
    # "auto" settles on the encoder the CI runner's ffmpeg has.
    assert status.encoder == "libx264"


def test_with_a_startup_time_it_says_starting_first(fake_clock: FakeClock) -> None:
    pipeline = FakeCameraPipeline(spec(), fake_clock, LOGGER, startup_seconds=1.0)
    pipeline.start()

    assert pipeline.status().state == PipelineState.STARTING
    fake_clock.advance(1.0)
    assert pipeline.status().state == PipelineState.RUNNING


def test_it_records_windows_and_keeps_the_last_through_a_failure(fake_clock: FakeClock) -> None:
    pipeline = FakeCameraPipeline(spec(), fake_clock, LOGGER)
    pipeline.start()
    pipeline.set_window(PipelineWindow(0.0, 0.0, 0.5, 0.5))
    pipeline.set_window(PipelineWindow(0.25, 0.25, 0.5, 0.5))

    pipeline.fail("camera not found: EMEET SmartCam S600")

    status = pipeline.status()
    assert status.state == PipelineState.RESTARTING
    assert status.reason == "camera not found: EMEET SmartCam S600"
    assert pipeline.last_window == PipelineWindow(0.25, 0.25, 0.5, 0.5)
    pipeline.recover()
    assert pipeline.status().state == PipelineState.RUNNING


def test_a_snapshot_is_a_whole_frame_jpeg_only_while_running(fake_clock: FakeClock) -> None:
    pipeline = FakeCameraPipeline(spec(), fake_clock, LOGGER)
    assert pipeline.latest_snapshot() is None  # not started: no picture

    pipeline.start()
    picture = pipeline.latest_snapshot()

    # About 320 pixels wide, with the source's 16:9 shape (rounded to 8-pixel blocks).
    assert picture is not None and picture.startswith(b"\xff\xd8")
    assert jpeg_size(picture) == (320, 176)


def test_the_factory_remembers_what_it_made(fake_clock: FakeClock) -> None:
    pipelines = FakePipelines()

    made = pipelines(spec(), fake_clock, LOGGER)

    assert pipelines.made == [made]
    assert pipelines.for_device(FAKE_DEVICES[0].device) is made
    assert pipelines.for_device("no such device") is None


def test_the_fake_devices_are_two_s600s_with_their_modes() -> None:
    devices = FakeCaptureDevices()

    listed = devices.list_capture_devices()

    assert len(listed) == 2 and listed[0].name == listed[1].name
    assert devices.capture_modes(listed[0].device)[0].width == 3840
    assert devices.capture_modes("unplugged") == []
