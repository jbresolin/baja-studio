"""The ffmpeg command line of one camera pipeline.

One process does everything for a camera (WP15 brief §3):

    camera ──► split ─┬─► zmq ─► crop@window ─► scale 1920x1080 ─► encoder ─► MPEG-TS ─► SRT
                      └─► fps=1 ─► scale 320 wide ─► JPEG ─► stdout (snapshots)

- **Input**: DirectShow at the camera's mode, MJPEG asked for by name
  (`-vcodec`, which also picks the decoder: `mjpeg` on the CPU, or a
  hardware one). Its real-time buffer is kept small (`DSHOW_BUFFER`) so a
  pipeline that falls behind drops frames instead of falling seconds behind.
- **Window**: the `zmq` filter receives crop commands (`window_sender.py`)
  and hands them to the crop filter at the next frame; the crop's options
  are made so any part of a change is a correct window (`crop_commands.py`).
  The scale after it copes with the crop's size changing between frames,
  provided a `null` filter separates them (see `filter_graph`).
- **Output**: constant frame rate, the encoder's live settings
  (`encoders.py`), MPEG-TS to the output URL as given (SRT caller with the
  URL's latency).
- **Snapshots**: the uncropped frame once a second, as JPEGs in a
  multipart stream on stdout (`snapshots.py`), which has their lengths.
- **Reports**: progress every second on stderr (`-progress pipe:2`), mixed
  with warnings and errors (`progress.py`, `ffmpeg_log.py`).
"""

from baja_studio.node.video.crop_commands import CropPixels, crop_filter
from baja_studio.node.video.encoders import PROFILES
from baja_studio.node.video.spec import PipelineSpec
from baja_studio.node.video.synthetic_sources import is_synthetic, synthetic_input_arguments

# DirectShow keeps this much video waiting for ffmpeg. 4K MJPEG is about
# 0.5-1 MB a frame, so this is about one second: enough for a hiccup, and
# a pipeline that cannot keep up drops frames (counted) rather than lag.
DSHOW_BUFFER = "32M"
SNAPSHOT_WIDTH = 320
SNAPSHOT_FPS = 1
# JPEG quality for snapshots (2 best .. 31 worst); 5 keeps them about 10 kB.
SNAPSHOT_QUALITY = 5
ZMQ_HOST = "127.0.0.1"


def input_arguments(spec: PipelineSpec) -> list[str]:
    """How ffmpeg opens the camera (or the synthetic source standing in for it)."""
    if is_synthetic(spec.device):
        return synthetic_input_arguments(
            spec.device, spec.input_width, spec.input_height, spec.input_fps
        )
    return [
        "-f", "dshow",
        "-rtbufsize", DSHOW_BUFFER,
        "-video_size", f"{spec.input_width}x{spec.input_height}",
        "-framerate", f"{spec.input_fps:g}",
        "-vcodec", spec.decoder,
        "-i", f"video={spec.device}",
    ]  # fmt: skip


def zmq_bind_option(port: int) -> str:
    """The zmq filter's address, its colons escaped twice (once for the graph, once for options)."""
    return f"bind_address=tcp\\\\://{ZMQ_HOST}\\\\:{port}"


def filter_graph(spec: PipelineSpec, encoder: str, window: CropPixels, zmq_port: int) -> str:
    """The `-filter_complex` graph: a live branch `[live]` and a snapshot branch `[snap]`."""
    pixel_format = PROFILES[encoder].pixel_format
    live = ",".join(
        [
            f"zmq={zmq_bind_option(zmq_port)}",
            crop_filter(window, spec.output_width, spec.output_height),
            # Measured with ffmpeg 6.1 (ADR 0023): a crop command that changes the
            # size rewrites the link into the next filter. A scale right there then
            # misses the change and ffmpeg stalls for good. A `null` (which does
            # nothing) in between keeps that link as it was, so the scale sees
            # frames of a new size and reconfigures itself, as it is made to.
            "null",
            f"scale={spec.output_width}:{spec.output_height}",
            "setsar=1",
            f"format={pixel_format}",
        ]
    )
    # yuvj420p is what ffmpeg's JPEG encoder takes in every version we use.
    snapshot = f"fps={SNAPSHOT_FPS},scale={SNAPSHOT_WIDTH}:-2,format=yuvj420p"
    return f"[0:v]split=2[full][whole];[full]{live}[live];[whole]{snapshot}[snap]"


def keyframe_interval_frames(spec: PipelineSpec) -> int:
    """Frames between keyframes (at least 1)."""
    return max(1, round(spec.input_fps * spec.keyframe_seconds))


def build_ffmpeg_command(
    spec: PipelineSpec, encoder: str, window: CropPixels, zmq_port: int
) -> list[str]:
    """The whole command line for one run of the pipeline, starting at `window`."""
    profile = PROFILES[encoder]
    return [
        spec.ffmpeg_path,
        "-hide_banner", "-nostdin", "-loglevel", "warning",
        # Progress as key=value lines every second, on stderr; no status line.
        "-nostats", "-stats_period", "1", "-progress", "pipe:2",
        *input_arguments(spec),
        "-filter_complex", filter_graph(spec, encoder, window, zmq_port),
        # The live feed first: ffmpeg's progress counts the first output's frames.
        "-map", "[live]",
        "-r", f"{spec.input_fps:g}", "-fps_mode", "cfr",
        *profile.arguments(spec.bitrate_kbps, keyframe_interval_frames(spec)),
        "-f", "mpegts", spec.output_url,
        "-map", "[snap]",
        "-c:v", "mjpeg", "-q:v", str(SNAPSHOT_QUALITY),
        "-f", "mpjpeg", "pipe:1",
    ]  # fmt: skip
