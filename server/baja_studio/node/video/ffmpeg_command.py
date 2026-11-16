"""The ffmpeg command line of one camera pipeline.

One process does everything for a camera (WP15 brief §3):

    camera ──► split ─┬─► zmq ─► crop@window ─► scale 1920x1080 ─► encoder ─► MPEG-TS ─► SRT
                      └─► fps=1 ─► scale 320 wide ─► JPEG ─► stdout (snapshots)

- **Input**: DirectShow at the camera's mode, MJPEG asked for by name
  (`-vcodec`, which also picks the decoder: `mjpeg` on the CPU, or a
  hardware one). Its real-time buffer holds about a quarter of a second
  (`capture_buffer_bytes`), so a pipeline that falls behind drops frames
  (counted) instead of falling seconds behind, and `LIVE_INPUT_OPTIONS` throw
  away the frames ffmpeg reads while it starts instead of queueing them.
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

# DirectShow keeps up to `rtbufsize` bytes of video waiting for ffmpeg, and
# anything waiting there is delay. It drops frames (counted, see progress.py)
# as the buffer passes 62 % full, so the delay it can add is about the time
# this many bytes take to arrive. That time is what we choose:
CAPTURE_BUFFER_SECONDS = 0.25
# ...from an MJPEG frame's size, which depends on the camera and the scene:
# about 0.5-1 MB at 4K (3840x2160 = 8.3 million pixels). The larger figure is
# used, so a busy scene holds at most a quarter second; a plain one less.
MJPEG_BYTES_PER_PIXEL = 0.12
# Never fewer than this many frames, so one late frame is not a drop.
CAPTURE_BUFFER_MIN_FRAMES = 4
# (It was 32 MB until the first laptop bench, 16 Nov: one to two seconds of
# 4K. A buffer that filled while ffmpeg started never emptied on a laptop
# that only just keeps up, which kept the picture that far behind.)
SNAPSHOT_WIDTH = 320
SNAPSHOT_FPS = 1
# JPEG quality for snapshots (2 best .. 31 worst); 5 keeps them about 10 kB.
SNAPSHOT_QUALITY = 5
ZMQ_HOST = "127.0.0.1"


# How ffmpeg starts reading a live camera without falling behind for good
# (measured 16 Nov, ADR 0023 "Delay"): while it starts (looking at the stream,
# opening the encoder) the camera keeps sending, about two seconds' worth.
# Queued, those frames are delay that a laptop which only just keeps up at
# 30 fps never catches up on. `nobuffer` drops the frames read while looking
# at the stream instead of queueing them, and `analyzeduration 0` keeps that
# look short. (`probesize 32` as well would make it shorter still, but then
# a raw MJPEG stream did not open at all in the test; DirectShow gives the
# picture size itself, so the default probe size costs little.)
LIVE_INPUT_OPTIONS = ("-fflags", "nobuffer", "-analyzeduration", "0")
# QuickSync's MJPEG decoder keeps 4 frames in flight by default ("the higher
# the value the higher the latency", ffmpeg's own help): 1 here.
DECODER_OPTIONS = {"mjpeg_qsv": ("-async_depth", "1")}


def input_arguments(spec: PipelineSpec) -> list[str]:
    """How ffmpeg opens the camera (or the synthetic source standing in for it)."""
    if is_synthetic(spec.device):
        # The same start-up options, so the tests run them with the real ffmpeg.
        return [
            *LIVE_INPUT_OPTIONS,
            *synthetic_input_arguments(
                spec.device, spec.input_width, spec.input_height, spec.input_fps
            ),
        ]
    return [
        "-f", "dshow",
        "-rtbufsize", str(capture_buffer_bytes(spec)),
        *LIVE_INPUT_OPTIONS,
        "-video_size", f"{spec.input_width}x{spec.input_height}",
        "-framerate", f"{spec.input_fps:g}",
        "-vcodec", spec.decoder,
        *DECODER_OPTIONS.get(spec.decoder, ()),
        "-i", f"video={spec.device}",
    ]  # fmt: skip


def capture_buffer_bytes(spec: PipelineSpec) -> int:
    """DirectShow's real-time buffer for this mode: about `CAPTURE_BUFFER_SECONDS` of MJPEG.

    4K at 30 fps: 0.25 s x 30 frames x 8.3 Mpixel x 0.12 bytes = about 7.5 MB.
    """
    frame_bytes = spec.input_width * spec.input_height * MJPEG_BYTES_PER_PIXEL
    frames = max(CAPTURE_BUFFER_MIN_FRAMES, CAPTURE_BUFFER_SECONDS * spec.input_fps)
    return round(frame_bytes * frames)


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
