"""Tests for `CameraPipeline`, the supervisor of one camera's ffmpeg (node/video/pipeline.py).

No ffmpeg runs here. The pipeline is given fakes (`fake_runs.py`): a
launcher whose "ffmpeg" the test drives by hand (it writes progress, log
lines and snapshots, and exits when told), a prober that says which
encoders work, and window senders that only record. Time is the fake clock:
the pipeline's thread waits on it, and the test moves it with
`step_fake_time`, which waits for the thread to be asleep before and after
each step, so the test always sees what the thread did.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.pipeline_parts import PipelineParts
from baja_studio.node.video.spec import PipelineSpec, PipelineState, SourceWindow
from tests.conftest import (
    FakeClock,
    module_worker_threads,
    step_fake_time,
    step_fake_time_until,
    wait_until_workers_sleep,
)
from tests.node.video.fake_runs import FakeLauncher, FakeProber, RecordingSenders

URL = "srt://127.0.0.1:8890?streamid=publish:laptop1:laptop1:s3cret-pw&pkt_size=1316"
SPEC = PipelineSpec(
    device="lavfi:testsrc2",
    input_width=3840,
    input_height=2160,
    input_fps=30,
    output_url=URL,
    label="left camera",
)
JPEG = b"\xff\xd8 a small picture \xff\xd9"
# The pipeline's thread is named "node-video.<camera>"; these find it.
PIPELINE_THREADS = module_worker_threads("node-video")
SRT_UNREACHABLE = "[srt @ 0x1] Connection to srt://127.0.0.1:8890 failed: Input/output error"


@dataclass
class Bench:
    """A pipeline and its fakes."""

    pipeline: CameraPipeline
    launcher: FakeLauncher
    prober: FakeProber
    senders: RecordingSenders
    clock: FakeClock
    installed: list[bool]

    def step(self, seconds: float) -> None:
        """Let `seconds` of fake time pass, in quarter seconds (the pipeline's watch interval)."""
        step_fake_time(self.clock, seconds, PIPELINE_THREADS, step=0.25)

    def settle(self) -> None:
        """Wait until the pipeline's thread is asleep (it has done all it can for now)."""
        wait_until_workers_sleep(self.clock, PIPELINE_THREADS)

    def state(self) -> PipelineState:
        return self.pipeline.status().state


@pytest.fixture
def bench(fake_clock: FakeClock) -> Iterator[Bench]:
    """A started pipeline whose encoder test finds only x264 working (no GPU here)."""
    launcher = FakeLauncher()
    prober = FakeProber(working={"libx264"})
    senders = RecordingSenders()
    installed = [True]
    parts = PipelineParts(
        launch=launcher,
        probe_encoder=prober,
        make_sender=senders,
        free_port=lambda: 5555,
        ffmpeg_exists=lambda path: installed[0],
    )
    pipeline = CameraPipeline(SPEC, fake_clock, logging.getLogger("test.pipeline"), parts)
    bench = Bench(pipeline, launcher, prober, senders, fake_clock, installed)
    yield bench
    pipeline.stop()


def started(bench: Bench) -> Bench:
    bench.pipeline.start()
    bench.settle()
    return bench


def test_before_start_it_is_stopped_and_starts_nothing(bench: Bench) -> None:
    assert bench.state() == PipelineState.STOPPED
    assert bench.launcher.runs == []


def test_start_tests_the_encoders_and_launches_ffmpeg_at_the_whole_frame(bench: Bench) -> None:
    started(bench)
    # "auto": NVENC, QuickSync and AMF were tried and failed; x264 works.
    assert bench.prober.asked == ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
    command = bench.launcher.latest.command
    assert command[command.index("-c:v") + 1] == "libx264"
    # ffmpeg starts on the whole 4K frame, and a sender is ready for the next window.
    sender = bench.senders.made[0]
    assert sender.started and sender.port == 5555
    assert sender.starting_window == CropPixels(3840, 1920, 1080)
    status = bench.pipeline.status()
    assert status.state == PipelineState.STARTING
    assert status.encoder == "libx264"


def test_it_is_running_once_frames_come_out_with_the_rates_ffmpeg_reports(bench: Bench) -> None:
    run = started(bench).launcher.latest
    # Two progress blocks a second apart: 30 frames each, 33 000 bytes a frame.
    run.progress()
    bench.step(1.0)
    run.progress()
    bench.step(0.25)
    status = bench.pipeline.status()
    assert status.state == PipelineState.RUNNING
    assert status.fps_in == pytest.approx(30.0)
    assert status.fps_out == pytest.approx(30.0)
    # 30 frames x 33 000 bytes x 8 bits in one second = 7 920 kb/s.
    assert status.bitrate_kbps == pytest.approx(7920.0)
    assert status.window_apply_ms == 25.0
    assert status.reason == ""


def test_a_window_goes_to_the_running_ffmpeg_at_once(bench: Bench) -> None:
    started(bench)
    # Any thread may call this; it hands the window to the sender and returns.
    bench.pipeline.set_window(SourceWindow(0.0, 0.0, 0.5, 0.5))
    assert bench.senders.made[0].offered == [CropPixels(1920, 960, 540)]


def test_a_failed_run_says_why_and_is_restarted_after_1_then_2_then_4_seconds(
    bench: Bench,
) -> None:
    started(bench)
    for wait in (1, 2, 4):
        bench.launcher.latest.exit(251, SRT_UNREACHABLE)
        bench.step(0.25)  # the pipeline notices at its next look
        status = bench.pipeline.status()
        assert status.state == PipelineState.RESTARTING
        assert (
            status.reason == f"SRT receiver unreachable: 127.0.0.1:8890 — trying again in {wait} s"
        )
        runs_before = len(bench.launcher.runs)
        # Not before the wait is over...
        bench.step(wait - 0.25)
        assert len(bench.launcher.runs) == runs_before
        # ...and right after it, a new ffmpeg.
        bench.step(0.25)
        assert len(bench.launcher.runs) == runs_before + 1
    assert bench.pipeline.status().restarts == 3
    # The encoders were tested only once: an SRT failure says nothing about them.
    assert bench.prober.asked.count("libx264") == 1


def test_a_window_set_while_restarting_is_where_the_next_run_starts(bench: Bench) -> None:
    started(bench)
    bench.launcher.latest.exit(1, SRT_UNREACHABLE)
    bench.step(0.25)
    # Nobody to send to while waiting: the window is kept...
    bench.pipeline.set_window(SourceWindow(0.5, 0.5, 0.5, 0.5))
    bench.step(1.0)
    # ...and the next ffmpeg starts on it (command line and sender agree).
    assert bench.senders.made[-1].starting_window == CropPixels(1920, 2880, 1620)
    assert (
        "crop@window=w=1920:h=ow*1080/1920:x=2880-ow/2:y=1620-oh/2"
        in (
            bench.launcher.latest.command[
                bench.launcher.latest.command.index("-filter_complex") + 1
            ]
        )
    )


def test_a_run_that_went_well_for_ten_seconds_makes_the_next_wait_short_again(
    bench: Bench,
) -> None:
    started(bench)
    # Two quick failures: the next wait would be 4 s.
    for wait in (1, 2):
        bench.launcher.latest.exit(1, SRT_UNREACHABLE)
        bench.step(0.25 + wait)
    # This run delivers pictures for 11 seconds, then fails.
    run = bench.launcher.latest
    for _ in range(11):
        run.progress()
        bench.step(1.0)
    run.exit(1, SRT_UNREACHABLE)
    bench.step(0.25)
    assert bench.pipeline.status().reason.endswith("trying again in 1 s")


def test_a_failed_live_output_ends_the_run_at_once(bench: Bench) -> None:
    run = started(bench).launcher.latest
    run.progress()
    bench.step(0.25)
    # Measured: when the SRT receiver goes away, ffmpeg writes these lines and
    # closes the live output, but keeps running for the snapshots. The pipeline
    # does not wait for the stall watchdog: it ends the run at its next look.
    run.log("[vost#0:0/libx264 @ 0x55b983e93980] Error submitting a packet to the muxer: I/O error")
    run.log("[out#0/mpegts @ 0x55b983e92d80] Error muxing a packet")
    bench.step(0.25)
    assert run.stopped
    assert (
        bench.pipeline.status().reason == "SRT receiver lost: 127.0.0.1:8890 — trying again in 1 s"
    )


def test_a_picture_that_stops_for_five_seconds_is_restarted(bench: Bench) -> None:
    run = started(bench).launcher.latest
    run.progress()
    bench.step(0.25)
    assert bench.state() == PipelineState.RUNNING
    # ffmpeg is alive but no more frames come (a camera that froze).
    step_fake_time_until(
        bench.clock, lambda: bench.state() == PipelineState.RESTARTING, 6.0, PIPELINE_THREADS
    )
    assert run.stopped  # the stuck ffmpeg was stopped (and reaped)
    assert bench.pipeline.status().reason.startswith("the picture stopped for 5 s")


def test_a_run_with_no_picture_after_twenty_seconds_is_restarted(bench: Bench) -> None:
    run = started(bench).launcher.latest
    bench.step(19.75)
    assert bench.state() == PipelineState.STARTING
    bench.step(0.5)
    assert run.stopped
    assert bench.pipeline.status().reason.startswith("no picture 20 s after starting")


def test_without_ffmpeg_it_fails_and_looks_again_every_thirty_seconds(bench: Bench) -> None:
    bench.installed[0] = False
    started(bench)
    status = bench.pipeline.status()
    assert status.state == PipelineState.FAILED
    assert status.reason == "ffmpeg not found: ffmpeg — trying again in 30 s"
    assert bench.launcher.runs == []
    # Someone installs it; within 30 s the pipeline finds it and starts.
    bench.installed[0] = True
    bench.step(30.0)
    assert len(bench.launcher.runs) == 1


def test_with_no_working_encoder_it_fails_naming_each_encoder(bench: Bench) -> None:
    bench.prober.working.clear()
    started(bench)
    status = bench.pipeline.status()
    assert status.state == PipelineState.FAILED
    assert status.reason.startswith("no H.264 encoder works (NVIDIA NVENC: no device;")
    assert bench.launcher.runs == []


def test_after_an_encoder_failure_the_encoders_are_tested_again(bench: Bench) -> None:
    started(bench)
    bench.launcher.latest.exit(1, "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory")
    bench.step(1.25)
    # The second run started after testing every encoder again (x264 twice now).
    assert bench.prober.asked.count("libx264") == 2
    assert len(bench.launcher.runs) == 2


def test_a_launch_that_cannot_find_ffmpeg_is_a_failure(bench: Bench) -> None:
    bench.launcher.missing = True
    started(bench)
    status = bench.pipeline.status()
    assert status.state == PipelineState.FAILED
    assert status.reason.startswith("ffmpeg could not be started (ffmpeg): no such file")


def test_a_bug_in_the_pipeline_is_logged_and_retried_not_fatal(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    # Something unexpected goes wrong while starting a run (a bug of ours).
    bench.launcher.bug = KeyError("surprise")
    started(bench)
    status = bench.pipeline.status()
    # The camera does not silently stay "starting": it says what happened and retries.
    assert status.state == PipelineState.RESTARTING
    assert status.reason == "internal error: 'surprise' — trying again in 1 s"
    assert any(record.exc_info for record in caplog.records)
    bench.launcher.bug = None
    bench.step(1.0)
    assert len(bench.launcher.runs) == 1


def test_ffmpeg_is_stopped_even_when_something_fails_after_it_started(bench: Bench) -> None:
    # The window sender cannot be made (a bug, a ZeroMQ error) after ffmpeg is
    # already running. That ffmpeg must not be left behind holding the camera.
    bench.senders.broken = True
    started(bench)
    assert bench.launcher.runs[0].stopped
    assert bench.pipeline.status().reason.startswith("internal error: no sender today")


def test_snapshots_are_served_while_running_and_dropped_when_ffmpeg_fails(bench: Bench) -> None:
    run = started(bench).launcher.latest
    assert bench.pipeline.latest_snapshot() is None  # none yet
    run.snapshot(JPEG)
    assert bench.pipeline.latest_snapshot() == JPEG
    # A newer one replaces it (only the newest is kept: memory never grows).
    run.snapshot(JPEG + b"2")
    assert bench.pipeline.latest_snapshot() == JPEG + b"2"
    # When ffmpeg fails, the picture would be stale: it is dropped.
    run.exit(1, SRT_UNREACHABLE)
    bench.step(0.25)
    assert bench.pipeline.latest_snapshot() is None


def test_the_log_tail_has_no_password_no_progress_and_no_harmless_warnings(bench: Bench) -> None:
    run = started(bench).launcher.latest
    run.progress()
    run.log("[swscaler @ 0x5] deprecated pixel format used, make sure you did set range correctly")
    run.log(f"[srt @ 0x1] Connection to {URL} failed: Input/output error")
    tail = bench.pipeline.log_tail()
    assert tail[0] == "--- ffmpeg starting (x264 (CPU), left camera)"
    assert tail[1:] == [
        "[srt @ 0x1] Connection to srt://127.0.0.1:8890?streamid=publish:laptop1:laptop1:***"
        "&pkt_size=1316 failed: Input/output error"
    ]
    assert not any("s3cret-pw" in line for line in tail)


def test_directshow_drops_are_counted_but_kept_out_of_the_log(bench: Bench) -> None:
    run = started(bench).launcher.latest
    run.progress()
    for _ in range(3):
        run.log(
            "[dshow @ 0x1] real-time buffer [EMEET SmartCam S600] [video input] too full or "
            "near too full (101% of size: 33554432 [rtbufsize parameter])! frame dropped!"
        )
    assert bench.pipeline.status().dropped_frames == 3
    assert len(bench.pipeline.log_tail()) == 1  # only the start marker


def test_stop_ends_ffmpeg_and_the_sender_and_says_stopped(bench: Bench) -> None:
    run = started(bench).launcher.latest
    bench.pipeline.stop()
    assert run.stopped
    assert bench.senders.made[0].stopped
    assert bench.state() == PipelineState.STOPPED
    # A window set while stopped is kept for the next start.
    bench.pipeline.set_window(SourceWindow(0.0, 0.0, 0.5, 0.5))
    started(bench)
    assert bench.senders.made[-1].starting_window == CropPixels(1920, 960, 540)


def test_stop_during_a_wait_returns_without_waiting_it_out(bench: Bench) -> None:
    bench.installed[0] = False
    started(bench)  # failed, waiting 30 s
    # stop() returns at once although no fake time passes.
    bench.pipeline.stop()
    assert bench.state() == PipelineState.STOPPED
