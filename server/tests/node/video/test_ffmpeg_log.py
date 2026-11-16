"""Tests for ffmpeg's log: secrets removed, a short tail, why a run ended (ffmpeg_log.py).

The SRT URL a camera sends to carries the feed's password. ffmpeg repeats
the URL in its error messages, so every line is scrubbed before anyone can
see it. When ffmpeg stops, its last lines are turned into a reason an
operator understands.

The sample lines come from ffmpeg itself: the SRT ones were recorded from
ffmpeg 6.1 against MediaMTX (receiver absent, then killed); the DirectShow
and zmq ones are the messages in ffmpeg's source (`libavdevice/dshow.c`,
`libavfilter/f_zmq.c`).
"""

from baja_studio.node.video.ffmpeg_log import (
    LOG_TAIL_LINES,
    FailureKind,
    LogTail,
    explain_failure,
    receiver_address,
    redact,
    url_secrets,
)

URL = "srt://192.168.1.10:8890?streamid=publish:laptop1:laptop1:s3cret-pw&pkt_size=1316&latency=200000"
CAMERA = "EMEET SmartCam S600"


def explain(*lines: str, exit_code: int | None = 1) -> tuple[FailureKind, str]:
    failure = explain_failure(exit_code, list(lines), CAMERA, URL)
    return failure.kind, failure.reason


def test_the_password_of_an_srt_stream_id_is_a_secret() -> None:
    # MediaMTX's stream id is publish:<feed>:<user>:<password>.
    assert url_secrets(URL) == ["s3cret-pw"]
    # An SRT passphrase or an RTMP password are secrets too.
    assert url_secrets("srt://h:1?passphrase=longphrase123") == ["longphrase123"]
    assert url_secrets("rtmp://h/live?user=a&pass=pw1") == ["pw1"]
    # No password, no secret.
    assert url_secrets("srt://h:1?streamid=publish:laptop1") == []


def test_secrets_are_replaced_by_stars_in_any_line() -> None:
    line = f"[srt @ 0x55fb] Connection to {URL} failed: Input/output error"
    clean = redact(line, url_secrets(URL))
    assert "s3cret-pw" not in clean
    assert "streamid=publish:laptop1:laptop1:***&pkt_size" in clean


def test_a_stream_id_password_is_removed_even_when_not_known_in_advance() -> None:
    # A safety net: any `streamid=publish:a:b:<password>` loses its password.
    assert redact("x streamid=publish:cam:cam:other-pw y", []) == "x streamid=publish:cam:cam:*** y"


def test_the_receiver_address_shows_host_and_port_only() -> None:
    assert receiver_address(URL) == "192.168.1.10:8890"


def test_the_log_tail_keeps_the_last_lines_scrubbed() -> None:
    tail = LogTail(["s3cret-pw"])
    for number in range(LOG_TAIL_LINES + 5):
        tail.add(f"line {number} s3cret-pw\n")
    lines = tail.lines()
    # Only the newest LOG_TAIL_LINES are kept, oldest first, without the secret.
    assert len(lines) == LOG_TAIL_LINES
    assert lines[0] == "line 5 ***"
    assert lines[-1] == f"line {LOG_TAIL_LINES + 4} ***"


def test_no_srt_receiver_listening_reads_as_unreachable() -> None:
    # Recorded: ffmpeg 6.1 sending to a port where nothing listens (3 s, then exit 251).
    kind, reason = explain(
        "[srt @ 0x55fb00fc8880] Connection to srt://127.0.0.1:18999?streamid=publish:cam1"
        "&pkt_size=1316&latency=200000 failed: Input/output error",
        "[out#0/mpegts @ 0x55fb00fc4700] Error opening output srt://127.0.0.1:18999?...: "
        "Input/output error",
        "Error opening output files: Input/output error",
        exit_code=251,
    )
    assert kind == FailureKind.RECEIVER
    assert reason == "SRT receiver unreachable: 192.168.1.10:8890"


def test_a_receiver_that_went_away_reads_as_lost() -> None:
    # Recorded: MediaMTX killed while ffmpeg 6.1 was sending (5 s later, exit 251).
    kind, reason = explain(
        "[vost#0:0/libx264 @ 0x558c915d9100] Error submitting a packet to the muxer: "
        "Input/output error",
        "[out#0/mpegts @ 0x558c915d7bc0] Error muxing a packet",
        "[out#0/mpegts @ 0x558c915d7bc0] Error writing trailer: Input/output error",
        exit_code=251,
    )
    assert (kind, reason) == (FailureKind.RECEIVER, "SRT receiver lost: 192.168.1.10:8890")


def test_a_missing_camera_reads_as_not_found() -> None:
    kind, reason = explain(
        "[dshow @ 000001e2] Could not find video device with name [EMEET SmartCam S600] "
        "among source devices of type video.",
        "[in#0 @ 000001e2] Error opening input: I/O error",
    )
    assert (kind, reason) == (FailureKind.CAMERA, f"camera not found: {CAMERA}")


def test_a_camera_used_by_another_program_reads_as_busy() -> None:
    kind, reason = explain(
        "[dshow @ 0000020f] Could not run graph (sometimes caused by a device already in use "
        "by other application)"
    )
    assert kind == FailureKind.CAMERA
    assert reason.startswith("camera busy")


def test_a_mode_the_camera_does_not_have_reads_as_refused() -> None:
    kind, reason = explain("[dshow @ 0000020f] Could not set video options")
    assert (kind, reason) == (FailureKind.CAMERA, f"camera refused the mode: {CAMERA}")


def test_an_encoder_that_cannot_start_names_its_complaint() -> None:
    kind, reason = explain(
        "[h264_nvenc @ 0x563da4eea040] OpenEncodeSessionEx failed: out of memory (10)",
        "[vost#0:0/h264_nvenc @ 0x563da4ee9c80] Error while opening encoder",
    )
    assert kind == FailureKind.ENCODER
    # The `[component @ address]` prefix is dropped: it means nothing to an operator.
    assert reason == "encoder failed: OpenEncodeSessionEx failed: out of memory (10)"


def test_a_taken_zmq_port_reads_as_the_window_control_port() -> None:
    kind, reason = explain(
        "[Parsed_zmq_1 @ 0x55] Could not bind ZMQ socket to address 'tcp://127.0.0.1:5555': "
        "Address already in use"
    )
    assert kind == FailureKind.CONTROL
    assert reason.startswith("the window control port could not be opened: Could not bind")


def test_an_ffmpeg_without_directshow_says_so() -> None:
    # What Linux's ffmpeg says when asked for a DirectShow camera.
    kind, reason = explain("Unknown input format: 'dshow'")
    assert (kind, reason) == (
        FailureKind.CAMERA,
        "this ffmpeg cannot capture cameras (no DirectShow)",
    )


def test_anything_else_gives_the_exit_code_and_the_last_line() -> None:
    assert explain("[foo @ 0x1] Something odd", exit_code=1) == (
        FailureKind.OTHER,
        "ffmpeg stopped (exit code 1): Something odd",
    )
    assert explain(exit_code=None) == (FailureKind.OTHER, "ffmpeg stopped")


# --- Linux (V4L2) and the Rockchip hardware (the NanoPi, ADR 0024) ------------------------------
# The lines follow ffmpeg's V4L2 input (libavdevice/v4l2.c) and ffmpeg-rockchip's
# MPP/RGA code; the NanoPi bench sheet checks them against a real board.


def test_a_linux_camera_that_is_not_plugged_in_reads_as_not_found() -> None:
    kind, reason = explain(
        "[video4linux2,v4l2 @ 0xaaaad] Cannot open video device "
        "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usb-0:1:1.0-video-index0: "
        "No such file or directory"
    )
    assert (kind, reason) == (FailureKind.CAMERA, f"camera not found: {CAMERA}")


def test_a_linux_camera_held_by_another_program_reads_as_busy() -> None:
    kind, reason = explain(
        "[video4linux2,v4l2 @ 0xaaaad] ioctl(VIDIOC_STREAMON): Device or resource busy"
    )
    assert kind == FailureKind.CAMERA and reason.startswith("camera busy")


def test_a_linux_camera_the_service_may_not_open_says_which_group_it_needs() -> None:
    kind, reason = explain(
        "[video4linux2,v4l2 @ 0xaaaad] Cannot open video device /dev/video0: Permission denied"
    )
    assert kind == FailureKind.CAMERA and "group video" in reason


def test_a_linux_camera_that_cannot_do_the_mode_reads_as_refused() -> None:
    kind, reason = explain(
        "[video4linux2,v4l2 @ 0xaaaad] The V4L2 driver changed the video from 3840x2160 "
        "to 1920x1080"
    )
    assert (kind, reason) == (FailureKind.CAMERA, f"camera refused the mode: {CAMERA}")


def test_a_linux_camera_unplugged_while_streaming_reads_as_stopped_sending() -> None:
    kind, reason = explain("[video4linux2,v4l2 @ 0xaaaad] ioctl(VIDIOC_DQBUF): No such device")
    assert (kind, reason) == (FailureKind.CAMERA, f"camera stopped sending: {CAMERA}")


def test_a_rockchip_hardware_failure_is_an_encoder_failure() -> None:
    # An encoder failure makes the pipeline test the encoders again before the next run.
    kind, reason = explain("[h264_rkmpp @ 0xaaaad] Failed to init MPP context: -1")
    assert kind == FailureKind.ENCODER and "Failed to init MPP" in reason
    kind, _ = explain("[vpp_rkrga@window @ 0xaaaad] RGA scale ratio (16.0000x16.0000) exceeds")
    assert kind == FailureKind.ENCODER
