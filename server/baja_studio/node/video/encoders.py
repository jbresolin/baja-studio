"""The H.264 encoders a pipeline can use, their settings, and picking one that works.

Every encoder is set up the same way for a live feed: constant bitrate, a
keyframe every `keyframe_seconds` and nowhere else (no scene-cut keyframes,
so a fast pan cannot start a storm of them), no B-frames, and its
lowest-latency mode. The option names are each encoder's own in ffmpeg 9.

`auto` tries them in `AUTO_ORDER`: NVENC (NVIDIA, the XPS's GTX 1050 Ti),
QuickSync (Intel, the ThinkPad's iGPU), AMF (AMD), then x264 on the CPU as
the last resort. "Works" means a one-second test encode succeeds: an encoder
listed by ffmpeg can still fail on a laptop without that GPU or driver.

On a Rockchip board (the NanoPi, ADR 0024) `auto` means Rockchip's MPP
encoder (`ROCKCHIP_AUTO_ORDER`): its test encode runs the very hardware path
of the live feed (upload, RGA crop and scale, MPP), and then also checks that
this ffmpeg can move the RGA's crop while running (the patched build).
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from baja_studio.node.video.ffmpeg_process import NO_CONSOLE_WINDOW
from baja_studio.node.video.rockchip import runtime_crop_problem
from baja_studio.node.video.spec import AUTO_ENCODER, ROCKCHIP_DECODER, SOFTWARE_DECODER

NVENC = "h264_nvenc"
QUICKSYNC = "h264_qsv"
AMF = "h264_amf"
X264 = "libx264"
RKMPP = "h264_rkmpp"
AUTO_ORDER = (NVENC, QUICKSYNC, AMF, X264)
# On a Rockchip board: the MPP encoder; x264 cannot keep up with 1080p30 on its cores.
ROCKCHIP_AUTO_ORDER = (RKMPP,)
# What the Rockchip test encode reads: a 4K picture, as the camera gives, uploaded to
# hardware and cropped and scaled by the RGA like the live feed.
ROCKCHIP_PROBE_SOURCE = "3840x2160"
ROCKCHIP_PROBE_FILTERS = "format=nv12,hwupload,vpp_rkrga=w=1920:h=1080:cw=1920:ch=1080:format=nv12"
# A test encode that takes longer than this is treated as not working.
PROBE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class EncoderProfile:
    """One encoder: the picture format it wants and its live-feed options."""

    name: str
    label: str
    pixel_format: str
    options: tuple[str, ...]
    # An option that must be given the keyframe interval too (AMF's header spacing).
    keyframe_option: str | None = None
    # True for an encoder that takes the Rockchip graph's hardware frames as they are.
    takes_hardware_frames: bool = False

    def arguments(self, bitrate_kbps: int, keyframe_interval_frames: int) -> list[str]:
        """ffmpeg's output options for this encoder at this bitrate and keyframe interval."""
        rate = f"{bitrate_kbps}k"
        interval = str(keyframe_interval_frames)
        extra = [self.keyframe_option, interval] if self.keyframe_option else []
        return [
            "-c:v", self.name, *self.options, *extra,
            "-b:v", rate, "-maxrate", rate, "-bufsize", rate,
            "-g", interval, "-bf", "0",
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
        ("-usage", "lowlatency", "-quality", "speed", "-rc", "cbr"),
        # Headers (SPS/PPS) with every keyframe, so a receiver can join at any one.
        # (`-header_insertion_mode` is HEVC/AV1 only: the pin test caught it.)
        keyframe_option="-header_spacing",
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
    RKMPP: EncoderProfile(
        RKMPP,
        "Rockchip MPP",
        # What it is tested with; in the live graph it gets the RGA's NV12 hardware frames.
        "nv12",
        ("-rc_mode", "CBR", "-profile:v", "high"),
        takes_hardware_frames=True,
    ),
}


def auto_order(decoder: str) -> tuple[str, ...]:
    """The encoders `auto` tries for a pipeline with this MJPEG decoder."""
    return ROCKCHIP_AUTO_ORDER if decoder == ROCKCHIP_DECODER else AUTO_ORDER


@dataclass(frozen=True)
class ProbeResult:
    """Whether a test encode worked, and ffmpeg's last words if not."""

    works: bool
    message: str = ""


EncoderProber = Callable[[str, str], ProbeResult]


def probe_command(ffmpeg_path: str, encoder: str) -> list[str]:
    """A one-second 1920x1080 test encode with `encoder`'s live options, thrown away."""
    profile = PROFILES[encoder]
    if encoder == RKMPP:
        return rockchip_probe_command(ffmpeg_path)
    return [
        ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:size=1920x1080:rate=30",
        "-frames:v", "30", "-vf", f"format={profile.pixel_format}",
        *profile.arguments(bitrate_kbps=8000, keyframe_interval_frames=30),
        "-f", "null", "-",
    ]  # fmt: skip


def rockchip_probe_command(ffmpeg_path: str) -> list[str]:
    """The Rockchip test encode: 4K uploaded, RGA crop and scale to 1080p, MPP H.264."""
    profile = PROFILES[RKMPP]
    return [
        ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-init_hw_device", "rkmpp=rk", "-filter_hw_device", "rk",
        "-f", "lavfi", "-i", f"color=black:size={ROCKCHIP_PROBE_SOURCE}:rate=30",
        "-frames:v", "30", "-vf", ROCKCHIP_PROBE_FILTERS,
        *profile.arguments(bitrate_kbps=8000, keyframe_interval_frames=30),
        "-f", "null", "-",
    ]  # fmt: skip


def probe_encoder(ffmpeg_path: str, encoder: str) -> ProbeResult:
    """Run the test encode for real (the default `EncoderProber`).

    The Rockchip encoder also needs the RGA's crop to move live: an ffmpeg
    without the node's patch encodes fine but would never follow the
    window, so it is not counted as working.
    """
    result = _run_probe(ffmpeg_path, encoder)
    if result.works and encoder == RKMPP:
        problem = runtime_crop_problem(ffmpeg_path)
        if problem:
            return ProbeResult(False, problem)
    return result


def _run_probe(ffmpeg_path: str, encoder: str) -> ProbeResult:
    """One test encode."""
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


def choose_encoder(
    requested: str,
    ffmpeg_path: str,
    prober: EncoderProber,
    decoder: str = SOFTWARE_DECODER,
) -> EncoderChoice:
    """The first encoder that passes the test encode: `requested`, or `auto_order` for "auto".

    A forced encoder is tested too, so a wrong choice in the settings shows
    as a clear reason instead of an ffmpeg that dies at every start. The
    Rockchip encoder needs the Rockchip graph (`decoder` mjpeg_rkmpp): forced
    on any other, it is refused with that reason.
    """
    if requested != AUTO_ENCODER and requested not in PROFILES:
        known = ", ".join((AUTO_ENCODER, *PROFILES))
        return EncoderChoice(None, ((requested, f"unknown encoder (use one of {known})"),))
    if requested == RKMPP and decoder != ROCKCHIP_DECODER:
        why = f"needs the Rockchip decoder ({ROCKCHIP_DECODER}, or auto on a Rockchip board)"
        return EncoderChoice(None, ((requested, why),))
    candidates = auto_order(decoder) if requested == AUTO_ENCODER else (requested,)
    failures: list[tuple[str, str]] = []
    for name in candidates:
        result = prober(ffmpeg_path, name)
        if result.works:
            return EncoderChoice(name, tuple(failures))
        failures.append((name, result.message))
    return EncoderChoice(None, tuple(failures))
