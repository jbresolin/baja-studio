"""The H.264 encoders a pipeline can use, their settings, and picking one that works.

Every encoder is set up the same way for a live feed: constant bitrate, a
keyframe every `keyframe_seconds` and nowhere else (no scene-cut keyframes,
so a fast pan cannot start a storm of them), no B-frames, and its
lowest-latency mode. The option names are each encoder's own in ffmpeg 9.

`auto` tries them in `AUTO_ORDER`: NVENC (NVIDIA, the XPS's GTX 1050 Ti),
QuickSync (Intel, the ThinkPad's iGPU), AMF (AMD), then x264 on the CPU as
the last resort. "Works" means a one-second test encode succeeds: an encoder
listed by ffmpeg can still fail on a laptop without that GPU or driver.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from baja_studio.node.video.ffmpeg_process import NO_CONSOLE_WINDOW
from baja_studio.node.video.spec import AUTO_ENCODER

NVENC = "h264_nvenc"
QUICKSYNC = "h264_qsv"
AMF = "h264_amf"
X264 = "libx264"
AUTO_ORDER = (NVENC, QUICKSYNC, AMF, X264)
# A test encode that takes longer than this is treated as not working.
PROBE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class EncoderProfile:
    """One encoder: the picture format it wants and its live-feed options."""

    name: str
    label: str
    pixel_format: str
    options: tuple[str, ...]

    def arguments(self, bitrate_kbps: int, keyframe_interval_frames: int) -> list[str]:
        """ffmpeg's output options for this encoder at this bitrate and keyframe interval."""
        rate = f"{bitrate_kbps}k"
        return [
            "-c:v", self.name, *self.options,
            "-b:v", rate, "-maxrate", rate, "-bufsize", rate,
            "-g", str(keyframe_interval_frames), "-bf", "0",
        ]  # fmt: skip


PROFILES: dict[str, EncoderProfile] = {
    NVENC: EncoderProfile(
        NVENC,
        "NVIDIA NVENC",
        "yuv420p",
        ("-preset", "p4", "-tune", "ll", "-rc", "cbr", "-zerolatency", "1", "-no-scenecut", "1"),
    ),
    QUICKSYNC: EncoderProfile(
        QUICKSYNC,
        "Intel QuickSync",
        # QuickSync encodes NV12 only; converting in the graph says so plainly.
        "nv12",
        ("-preset", "veryfast", "-look_ahead", "0", "-async_depth", "1"),
    ),
    AMF: EncoderProfile(
        AMF,
        "AMD AMF",
        "yuv420p",
        # Headers at every keyframe, so a receiver can join at any one.
        (
            "-usage",
            "lowlatency",
            "-quality",
            "speed",
            "-rc",
            "cbr",
            "-header_insertion_mode",
            "idr",
        ),
    ),
    X264: EncoderProfile(
        X264,
        "x264 (CPU)",
        "yuv420p",
        (
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-x264-params",
            "nal-hrd=cbr:force-cfr=1:scenecut=0",
        ),
    ),
}


@dataclass(frozen=True)
class ProbeResult:
    """Whether a test encode worked, and ffmpeg's last words if not."""

    works: bool
    message: str = ""


EncoderProber = Callable[[str, str], ProbeResult]


def probe_command(ffmpeg_path: str, encoder: str) -> list[str]:
    """A one-second 1920x1080 test encode with `encoder`'s live options, thrown away."""
    profile = PROFILES[encoder]
    return [
        ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:size=1920x1080:rate=30",
        "-frames:v", "30", "-vf", f"format={profile.pixel_format}",
        *profile.arguments(bitrate_kbps=8000, keyframe_interval_frames=30),
        "-f", "null", "-",
    ]  # fmt: skip


def probe_encoder(ffmpeg_path: str, encoder: str) -> ProbeResult:
    """Run the test encode for real (the default `EncoderProber`)."""
    try:
        finished = subprocess.run(
            probe_command(ffmpeg_path, encoder),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except FileNotFoundError:
        return ProbeResult(False, f"ffmpeg not found: {ffmpeg_path}")
    except subprocess.TimeoutExpired:
        return ProbeResult(False, f"no answer within {PROBE_TIMEOUT_SECONDS:.0f} s")
    if finished.returncode == 0:
        return ProbeResult(True)
    lines = finished.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return ProbeResult(False, probe_failure_message(encoder, lines, finished.returncode))


def probe_failure_message(encoder: str, lines: list[str], exit_code: int) -> str:
    """The most telling line of a failed test encode: the encoder's own complaint if any.

    ffmpeg ends with generic lines ("Nothing was written into output file");
    the encoder's first line ("Cannot load libcuda.so.1", "Error creating a
    MFX session") says why.
    """
    own = [line for line in lines if f"[{encoder} @" in line]
    if own:
        return own[0]
    return lines[-1] if lines else f"exit code {exit_code}"


def _label(name: str) -> str:
    return PROFILES[name].label if name in PROFILES else repr(name)


@dataclass(frozen=True)
class EncoderChoice:
    """The encoder picked (None if none works) and what each one tried said."""

    encoder: str | None
    failures: tuple[tuple[str, str], ...]

    def reason(self) -> str:
        """Operator words for "none works", naming each encoder's answer."""
        tried = "; ".join(f"{_label(name)}: {message}" for name, message in self.failures)
        return f"no H.264 encoder works ({tried})"


def choose_encoder(requested: str, ffmpeg_path: str, prober: EncoderProber) -> EncoderChoice:
    """The first encoder that passes the test encode: `requested`, or `AUTO_ORDER` for "auto".

    A forced encoder is tested too, so a wrong choice in the settings shows
    as a clear reason instead of an ffmpeg that dies at every start.
    """
    if requested != AUTO_ENCODER and requested not in PROFILES:
        known = ", ".join((AUTO_ENCODER, *PROFILES))
        return EncoderChoice(None, ((requested, f"unknown encoder (use one of {known})"),))
    candidates = AUTO_ORDER if requested == AUTO_ENCODER else (requested,)
    failures: list[tuple[str, str]] = []
    for name in candidates:
        result = prober(ffmpeg_path, name)
        if result.works:
            return EncoderChoice(name, tuple(failures))
        failures.append((name, result.message))
    return EncoderChoice(None, tuple(failures))
