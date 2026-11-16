"""Tests for the encoder table and the choice of encoder (node/video/encoders.py).

"Encoder" is the part that compresses the picture to H.264. A laptop may
have an NVIDIA GPU (NVENC), an Intel one (QuickSync) or an AMD one (AMF);
`auto` tries them in that order and falls back to the CPU (x264). Here the
"test encode" that decides whether one works is replaced by a fake prober
that answers from a list, so no ffmpeg is needed.
"""

from pathlib import Path

from baja_studio.node.video.encoders import (
    AMF,
    AUTO_ORDER,
    NVENC,
    PROFILES,
    QUICKSYNC,
    X264,
    ProbeResult,
    choose_encoder,
    probe_command,
    probe_encoder,
    probe_failure_message,
)


class ListProber:
    """A fake prober: only the encoders in `working` work; it records what it was asked."""

    def __init__(self, working: set[str]) -> None:
        self.working = working
        self.asked: list[str] = []

    def __call__(self, ffmpeg_path: str, encoder: str) -> ProbeResult:
        self.asked.append(encoder)
        if encoder in self.working:
            return ProbeResult(True)
        return ProbeResult(False, f"{encoder} unavailable")


def test_auto_tries_nvidia_then_intel_then_amd_then_the_cpu() -> None:
    assert AUTO_ORDER == (NVENC, QUICKSYNC, AMF, X264)


def test_auto_keeps_the_first_encoder_that_works() -> None:
    # On the ThinkPad (Intel graphics) NVENC fails and QuickSync works: QuickSync
    # is picked and AMF and x264 are never even tried.
    prober = ListProber(working={QUICKSYNC, X264})
    choice = choose_encoder("auto", "ffmpeg", prober)
    assert choice.encoder == QUICKSYNC
    assert prober.asked == [NVENC, QUICKSYNC]
    # What NVENC said is kept, so the node can log why it was not used.
    assert choice.failures == ((NVENC, "h264_nvenc unavailable"),)


def test_auto_falls_back_to_x264_when_no_gpu_encoder_works() -> None:
    choice = choose_encoder("auto", "ffmpeg", ListProber(working={X264}))
    assert choice.encoder == X264


def test_when_nothing_works_the_reason_names_every_encoder_s_answer() -> None:
    choice = choose_encoder("auto", "ffmpeg", ListProber(working=set()))
    assert choice.encoder is None
    reason = choice.reason()
    assert reason.startswith("no H.264 encoder works (")
    for name in AUTO_ORDER:
        assert f"{PROFILES[name].label}: {name} unavailable" in reason


def test_a_forced_encoder_is_tested_too_and_alone() -> None:
    # The settings say "h264_nvenc": only it is tried. If it fails, the
    # pipeline gets a clear reason instead of an ffmpeg dying at every start.
    prober = ListProber(working={X264})
    choice = choose_encoder(NVENC, "ffmpeg", prober)
    assert prober.asked == [NVENC]
    assert choice.encoder is None
    assert "NVIDIA NVENC: h264_nvenc unavailable" in choice.reason()


def test_an_unknown_encoder_name_is_refused_without_testing() -> None:
    prober = ListProber(working={X264})
    choice = choose_encoder("h265_magic", "ffmpeg", prober)
    assert choice.encoder is None and prober.asked == []
    assert "'h265_magic': unknown encoder (use one of auto, h264_nvenc" in choice.reason()


def test_every_encoder_gets_constant_bitrate_fixed_keyframes_and_no_b_frames() -> None:
    # For a live feed: the same bitrate as target, ceiling and buffer (constant
    # bitrate), a keyframe every 30 frames (1 s at 30 fps), and no B-frames
    # (they add delay).
    for profile in PROFILES.values():
        arguments = profile.arguments(bitrate_kbps=8000, keyframe_interval_frames=30)
        assert arguments[:2] == ["-c:v", profile.name]
        for option, value in (("-b:v", "8000k"), ("-maxrate", "8000k"), ("-bufsize", "8000k")):
            assert arguments[arguments.index(option) + 1] == value
        assert arguments[arguments.index("-g") + 1] == "30"
        assert arguments[arguments.index("-bf") + 1] == "0"


def test_each_encoder_uses_its_low_latency_settings_and_no_scene_cut_keyframes() -> None:
    # Scene-cut keyframes would turn a fast pan into a storm of big frames.
    assert "-no-scenecut" in PROFILES[NVENC].options
    assert PROFILES[NVENC].options[2:4] == ("-tune", "ll")
    assert "scenecut=0" in PROFILES[X264].options[-1]
    assert "zerolatency" in PROFILES[X264].options
    assert PROFILES[QUICKSYNC].options[-2:] == ("-async_depth", "1")
    assert "lowlatency" in PROFILES[AMF].options
    # AMF repeats the stream headers at every keyframe: its spacing is the keyframe interval.
    amf = PROFILES[AMF].arguments(bitrate_kbps=8000, keyframe_interval_frames=30)
    assert amf[amf.index("-header_spacing") + 1] == "30"
    # QuickSync only takes NV12 pictures; the others take yuv420p.
    assert PROFILES[QUICKSYNC].pixel_format == "nv12"
    assert {PROFILES[name].pixel_format for name in (NVENC, AMF, X264)} == {"yuv420p"}


def test_the_test_encode_is_one_second_of_1080p_thrown_away() -> None:
    command = probe_command("C:/node/ffmpeg/ffmpeg.exe", NVENC)
    assert command[0] == "C:/node/ffmpeg/ffmpeg.exe"
    assert "color=black:size=1920x1080:rate=30" in command
    assert command[command.index("-frames:v") + 1] == "30"
    # It uses the very options the pipeline uses, so "works" means "works like that".
    assert command[command.index("-c:v") + 1] == NVENC
    assert command[-3:] == ["-f", "null", "-"]


def test_a_failed_test_encode_reports_the_encoder_s_own_complaint() -> None:
    # Real ffmpeg 6.1 output on a machine without an NVIDIA driver: the useful
    # line is the encoder's own, not ffmpeg's last words.
    lines = [
        "[h264_nvenc @ 0x563da4eea040] Cannot load libcuda.so.1",
        "[vost#0:0/h264_nvenc @ 0x563da4ee9c80] Error while opening encoder - maybe incorrect",
        "Error while filtering: Operation not permitted",
        "[out#0/null @ 0x563da4ee8880] Nothing was written into output file",
    ]
    assert probe_failure_message(NVENC, lines, 255) == lines[0]
    # Without such a line the last line is used, and with no output the exit code.
    assert probe_failure_message(AMF, ["Option not found"], 8) == "Option not found"
    assert probe_failure_message(AMF, [], 8) == "exit code 8"


def test_testing_with_a_missing_ffmpeg_says_so(tmp_path: Path) -> None:
    # A real attempt to run a program that does not exist.
    result = probe_encoder(str(tmp_path / "no-ffmpeg-here"), X264)
    assert not result.works
    assert result.message.startswith("ffmpeg not found")
