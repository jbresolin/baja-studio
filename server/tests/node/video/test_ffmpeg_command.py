"""Tests for the pipeline's ffmpeg command line (node/video/ffmpeg_command.py).

ffmpeg is driven entirely by its command line: where to read the camera,
the "filter graph" that cuts and resizes the picture, the encoder and the
outputs. These tests read the command the pipeline builds and check each
part, without running ffmpeg (the rig tests run it).
"""

from dataclasses import replace

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.ffmpeg_command import (
    build_ffmpeg_command,
    capture_buffer_bytes,
    filter_graph,
    input_arguments,
    keyframe_interval_frames,
    zmq_bind_option,
)
from baja_studio.node.video.spec import PipelineSpec

URL = "srt://192.168.1.10:8890?streamid=publish:laptop1:laptop1:pw&pkt_size=1316&latency=200000"
CAMERA = PipelineSpec(
    device="@device_pnp_\\\\?\\usb#vid_328f&pid_00b8#7&2a52a0a5",
    input_width=3840,
    input_height=2160,
    input_fps=30,
    output_url=URL,
    ffmpeg_path="C:/Program Files/BajaCameraNode/ffmpeg/ffmpeg.exe",
)
WHOLE = CropPixels(3840, 1920, 1080)


def value_after(command: list[str], option: str) -> str:
    """The word that follows `option` in the command (its value)."""
    return command[command.index(option) + 1]


def test_a_webcam_is_read_through_directshow_in_mjpeg_at_its_mode() -> None:
    arguments = input_arguments(CAMERA)
    assert arguments[:2] == ["-f", "dshow"]
    assert value_after(arguments, "-video_size") == "3840x2160"
    assert value_after(arguments, "-framerate") == "30"
    # `-vcodec mjpeg` asks the camera for MJPEG and decodes it on the CPU.
    assert value_after(arguments, "-vcodec") == "mjpeg"
    # A small real-time buffer: falling behind drops frames instead of adding delay.
    assert value_after(arguments, "-rtbufsize") == str(capture_buffer_bytes(CAMERA))
    # Frames read while ffmpeg starts are thrown away, not kept as a backlog,
    # and it spends as little time as it can looking at the stream first.
    assert value_after(arguments, "-fflags") == "nobuffer"
    assert value_after(arguments, "-analyzeduration") == "0"
    # The CPU decoder takes no extra options.
    assert "-async_depth" not in arguments
    assert arguments[-2:] == ["-i", f"video={CAMERA.device}"]


def test_the_capture_buffer_holds_about_a_quarter_second_of_4k_mjpeg() -> None:
    # Whatever waits in DirectShow's buffer is delay. A 4K MJPEG frame is up
    # to about 1 MB, so a quarter second at 30 fps is about 7.5 MB, far less
    # than the 32 MB (one to two seconds) the node used before 16 Nov.
    size = capture_buffer_bytes(CAMERA)
    assert 7_000_000 < size < 8_000_000
    one_frame = 3840 * 2160 * 0.12
    assert size / one_frame == 7.5  # 0.25 s x 30 frames a second


def test_a_smaller_or_slower_mode_gets_a_smaller_buffer_but_never_under_four_frames() -> None:
    # 1080p is a quarter of the pixels of 4K: a quarter of the buffer.
    full_hd = replace(CAMERA, input_width=1920, input_height=1080)
    assert capture_buffer_bytes(full_hd) == round(capture_buffer_bytes(CAMERA) / 4)
    # At 5 fps a quarter second is not even 2 frames; it still holds 4, so
    # one late frame is not dropped.
    slow = replace(CAMERA, input_fps=5)
    assert capture_buffer_bytes(slow) == round(3840 * 2160 * 0.12 * 4)


def test_a_hardware_mjpeg_decoder_is_named_in_place_of_mjpeg() -> None:
    assert value_after(input_arguments(replace(CAMERA, decoder="mjpeg_qsv")), "-vcodec") == (
        "mjpeg_qsv"
    )


def test_quicksync_decodes_one_frame_at_a_time() -> None:
    # By default QuickSync keeps 4 frames in flight, which is 4 frames of delay.
    arguments = input_arguments(replace(CAMERA, decoder="mjpeg_qsv"))
    assert value_after(arguments, "-async_depth") == "1"
    # A decoder option goes before `-i`, with the decoder it is for.
    assert arguments.index("-async_depth") < arguments.index("-i")


def test_a_synthetic_camera_is_generated_in_real_time() -> None:
    arguments = input_arguments(replace(CAMERA, device="lavfi:testsrc2"))
    # `-re` makes ffmpeg read it at 30 frames a second, like a real camera; it
    # starts with the camera's start-up options, so the tests run those too.
    assert arguments == [
        *("-fflags", "nobuffer", "-analyzeduration", "0"),
        *("-re", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30"),
    ]


def test_the_zmq_address_has_its_colons_escaped_for_the_graph() -> None:
    # Inside a filter graph ':' separates options, so the address's colons are
    # escaped (twice: once for the graph, once for the filter's options).
    assert zmq_bind_option(5555) == "bind_address=tcp\\\\://127.0.0.1\\\\:5555"


def test_the_graph_splits_the_picture_into_the_live_feed_and_the_snapshots() -> None:
    graph = filter_graph(CAMERA, "libx264", WHOLE, 5555)
    live, snapshot = graph.split(";")[1:]
    # The live branch: commands in, crop, a do-nothing `null`, scale to 1080p, format.
    assert live.startswith("[full]zmq=")
    assert ",crop@window=w=3840:h=ow*1080/1920:x=1920-ow/2:y=1080-oh/2," in live
    assert live.endswith(",null,scale=1920:1080,setsar=1,format=yuv420p[live]")
    # The snapshot branch gets the *whole* frame (it splits off before the crop),
    # one picture a second, 320 pixels wide.
    assert snapshot == "[whole]fps=1,scale=320:-2,format=yuvj420p[snap]"


def test_the_null_filter_sits_between_crop_and_scale() -> None:
    # Measured (ADR 0023): without it, ffmpeg 6.1 stalls the first time a crop
    # command changes the size. This test keeps it from being "tidied" away.
    graph = filter_graph(CAMERA, "libx264", WHOLE, 5555)
    filters = graph.split(";")[1].split(",")
    crop_at = next(index for index, name in enumerate(filters) if name.startswith("crop@"))
    assert filters[crop_at + 1] == "null"
    assert filters[crop_at + 2].startswith("scale=")


def test_quicksync_gets_nv12_pictures() -> None:
    assert filter_graph(CAMERA, "h264_qsv", WHOLE, 5555).split(";")[1].endswith("format=nv12[live]")


def test_the_live_feed_is_the_first_output_and_goes_to_the_url_as_mpeg_ts() -> None:
    command = build_ffmpeg_command(CAMERA, "h264_nvenc", WHOLE, 5555)
    assert command[0] == CAMERA.ffmpeg_path
    first_map = command.index("-map")
    assert command[first_map + 1] == "[live]"
    # Constant 30 fps out, NVENC, MPEG-TS to the SRT URL, exactly as given.
    assert value_after(command[first_map:], "-r") == "30"
    assert value_after(command[first_map:], "-fps_mode") == "cfr"
    assert value_after(command[first_map:], "-c:v") == "h264_nvenc"
    assert command[command.index(URL) - 2 : command.index(URL)] == ["-f", "mpegts"]


def test_snapshots_go_to_stdout_as_multipart_jpeg() -> None:
    command = build_ffmpeg_command(CAMERA, "libx264", WHOLE, 5555)
    assert command[-9:] == [
        "-map", "[snap]", "-c:v", "mjpeg", "-q:v", "5", "-f", "mpjpeg", "pipe:1",
    ]  # fmt: skip


def test_progress_goes_to_stderr_every_second_and_ffmpeg_never_reads_stdin() -> None:
    command = build_ffmpeg_command(CAMERA, "libx264", WHOLE, 5555)
    assert value_after(command, "-progress") == "pipe:2"
    assert value_after(command, "-stats_period") == "1"
    # -nostdin: a service has no keyboard, and ffmpeg must not wait for one.
    assert "-nostdin" in command and "-nostats" in command
    assert value_after(command, "-loglevel") == "warning"


def test_keyframes_come_every_keyframe_seconds_worth_of_frames() -> None:
    assert keyframe_interval_frames(CAMERA) == 30
    assert keyframe_interval_frames(replace(CAMERA, input_fps=25, keyframe_seconds=2)) == 50
    # Never 0, whatever the settings say.
    assert keyframe_interval_frames(replace(CAMERA, keyframe_seconds=0)) == 1
