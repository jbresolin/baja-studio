"""Tests for the laptop benchmark's own logic (node/video/bench.py and bench_pass.py).

The benchmark itself runs real cameras for minutes; João runs it on the
laptops (bench sheet step 1). Here: reading its options, choosing the
camera mode, the operator's panning path, and the report it prints.
"""

from datetime import UTC, datetime

import pytest

from baja_studio.node.video.bench import best_mode, parse_arguments, parse_mode, print_camera
from baja_studio.node.video.bench_pass import CameraResult, PassResult, panning_window, udp_sink
from baja_studio.node.video.capture_devices import CaptureMode
from baja_studio.node.video.spec import PipelineState, PipelineStatus


def test_the_defaults_are_sixty_second_passes_with_two_cameras_and_auto_encoder() -> None:
    arguments = parse_arguments([])
    assert (arguments.seconds, arguments.cameras, arguments.encoder) == (60.0, 2, "auto")
    # "auto": the Rockchip hardware path on a NanoPi, the CPU on a laptop (ADR 0024).
    assert arguments.decoder == "auto"
    assert not arguments.synthetic and not arguments.skip_decoders


def test_a_mode_is_written_width_x_height_at_rate() -> None:
    assert parse_mode("3840x2160@30") == (3840, 2160, 30.0)
    assert parse_mode("1920x1080") == (1920, 1080, 30.0)
    assert parse_mode("") is None


def test_the_best_mode_is_the_biggest_mjpeg_mode_at_25_fps_or_more() -> None:
    modes = [
        CaptureMode("mjpeg", 3840, 2160, 15.0, 15.0),  # 4K, but only 15 fps
        CaptureMode("mjpeg", 2560, 1440, 30.0, 5.0),
        CaptureMode("yuyv422", 3840, 2160, 30.0, 5.0),  # raw 4K30 would not fit USB
    ]
    assert best_mode(modes) == (2560, 1440, 30.0)
    # A camera offering 60 fps is still run at 30 (what the studio wants).
    assert best_mode([CaptureMode("mjpeg", 1920, 1080, 60.0, 5.0)]) == (1920, 1080, 30.0)
    # Nothing known: 4K30.
    assert best_mode([]) == (3840, 2160, 30.0)


def test_the_panning_path_stays_inside_the_frame_between_1x_and_2x() -> None:
    for step in range(600):  # 20 seconds of updates at 30 a second
        window = panning_window(step / 30)
        assert 0.5 <= window.w <= 1.0
        assert 0.0 <= window.x <= 1.0 - window.w + 1e-9
        assert 0.0 <= window.y <= 1.0 - window.h + 1e-9


def test_bench_pipelines_send_to_a_local_udp_port() -> None:
    assert udp_sink(5000) == "udp://127.0.0.1:5000?pkt_size=1316"


def test_the_report_shows_rates_encoder_and_latencies(capsys: pytest.CaptureFixture[str]) -> None:
    status = PipelineStatus(
        state=PipelineState.RUNNING,
        reason="",
        fps_in=29.97,
        fps_out=30.0,
        dropped_frames=4,
        bitrate_kbps=8012.0,
        encoder="h264_qsv",
        since=datetime(2026, 11, 16, tzinfo=UTC),
        window_apply_ms=31.0,
    )
    print_camera(CameraResult("EMEET S600", status, [140.0, 160.0, 150.0], moves_not_seen=1))
    printed = capsys.readouterr().out
    assert "fps in 30.0, out 30.0, dropped 4, restarts 0" in printed
    assert "encoder h264_qsv, bitrate 8012 kb/s" in printed
    assert "window apply (median) 31 ms" in printed
    assert "move latency median 150 ms, max 160 ms (3 moves), 1 moves not seen" in printed


def test_cpu_use_is_summarised_as_mean_and_max() -> None:
    assert PassResult([], [40.0, 50.0, 60.0]).cpu_summary() == "mean 50 %, max 60 %"
    assert PassResult([], []).cpu_summary() == "not available"
