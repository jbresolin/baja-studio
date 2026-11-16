"""The Rockchip hardware path of a camera pipeline: the NanoPi R3S node (ADR 0024).

A 4K30 MJPEG webcam is too much for the R3S's four Cortex-A55 cores in
software (decoding alone would take all of them), so on a Rockchip board the
whole picture stays in hardware buffers from the camera to the network:

    camera ─► MPP decoder ─► split ─┬─► zmq ─► RGA crop+scale 1920x1080 ─► MPP H.264 ─► SRT
    (v4l2)    (mjpeg_rkmpp)         └─► fps=1 ─► RGA 480 wide ─► download ─► JPEG ─► stdout

- **Decode**: `mjpeg_rkmpp` (the VPU) gives DRM PRIME frames: dma-bufs that
  never pass through the CPU.
- **Crop and scale**: one `vpp_rkrga` filter (the RGA 2D engine) cuts the
  window out of the 4K frame and scales it to 1080p in one pass. Its crop
  options (`cw`, `cx`, `cy`) are moved live through the `zmq` filter, as the
  laptop's `crop` filter is; stock ffmpeg-rockchip cannot do that, so the
  node's ffmpeg carries a small patch
  (`installer/ffmpeg-rockchip/0001-vpp_rkrga-move-the-crop-while-running.patch`)
  that makes them runtime options. The output size never changes, so no
  filter after it is ever reconfigured (the laptop path needs a `null` for that).
- **Encode**: `h264_rkmpp` takes the RGA's frames as they are.
- **Snapshots**: the RGA shrinks the whole frame to 480 wide once a second; only that
  small picture is copied to the CPU and made a JPEG.

The window's options are written like the laptop's: the crop's height and
edges are expressions of its width and centre (`ch=cw*1080/1920`,
`cx=1700-cw/2`), so a zoom is one command and a half-applied change is still
a correct 16:9 window. The RGA scales between 1/8 and 8 times
(`RGA_SCALE_LIMIT`, checked again by the patch), so the narrowest window
is a 240 px wide crop for a 1080p output.

This module only builds command-line pieces and checks the board; the
pipeline (`pipeline.py`) runs them like any other.
"""

import math
import os
import subprocess
from pathlib import Path

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.ffmpeg_process import NO_CONSOLE_WINDOW
from baja_studio.node.video.spec import ROCKCHIP_DECODER, SOFTWARE_DECODER

# The crop filter's instance name in the graph; window commands are addressed to it.
RGA_FILTER_TARGET = "vpp_rkrga@window"
# The RGA scales by at most this factor either way (RGA2: 16, RGA3: 8; the patch uses 8).
RGA_SCALE_LIMIT = 8
# The RGA works on a 2-pixel grid for YUV pictures.
RGA_ALIGNMENT = 2
# The snapshot is shrunk by the RGA to this width (3840 / 8 = 480: within its limit),
# then to the node's usual 320 on the CPU, which is cheap at that size.
RGA_SNAPSHOT_WIDTH = 480
# Device nodes a Rockchip board with MPP (video codec) and RGA (2D engine) has.
ROCKCHIP_DEVICE_NODES = (Path("/dev/mpp_service"), Path("/dev/rga"))
# The hardware device ffmpeg opens for the synthetic camera's upload (`-init_hw_device`).
HW_DEVICE_NAME = "rk"
HELP_TIMEOUT_SECONDS = 10.0


def is_rockchip_board(device_nodes: tuple[Path, ...] = ROCKCHIP_DEVICE_NODES) -> bool:
    """True on a board with Rockchip's video codec and 2D engine (the NanoPi R3S)."""
    return all(node.exists() for node in device_nodes)


def resolve_decoder(requested: str, on_rockchip: bool) -> str:
    """`auto` -> the Rockchip decoder on a Rockchip board, else the CPU; others as they are."""
    if requested != "auto":
        return requested
    return ROCKCHIP_DECODER if on_rockchip else SOFTWARE_DECODER


def min_rga_crop_width(output_width: int) -> int:
    """The narrowest crop the RGA can scale up to `output_width` (even): 240 for 1080p."""
    narrowest = math.ceil(output_width / RGA_SCALE_LIMIT)
    return narrowest + narrowest % RGA_ALIGNMENT


# --- the input ------------------------------------------------------------------------------


def decoder_arguments() -> list[str]:
    """Decode MJPEG on the VPU into DRM PRIME frames (given before `-i`)."""
    return [
        "-hwaccel", "rkmpp",
        "-hwaccel_output_format", "drm_prime",
        "-c:v", ROCKCHIP_DECODER,
    ]  # fmt: skip


def synthetic_upload_arguments() -> list[str]:
    """For a synthetic camera: open the hardware so its pictures can be uploaded to it."""
    return ["-init_hw_device", f"rkmpp={HW_DEVICE_NAME}", "-filter_hw_device", HW_DEVICE_NAME]


# Put in front of the graph for a synthetic camera: software frames into hardware buffers.
SYNTHETIC_UPLOAD_FILTERS = "format=nv12,hwupload"


# --- the window -----------------------------------------------------------------------------


def _width_option(pixels: CropPixels) -> str:
    return str(pixels.width)


def _left_option(pixels: CropPixels) -> str:
    # `cw` is the crop's width: the left edge follows it, keeping the centre.
    return f"{pixels.centre_x}-cw/2"


def _top_option(pixels: CropPixels) -> str:
    return f"{pixels.centre_y}-ch/2"


def rga_window_filter(pixels: CropPixels, output_width: int, output_height: int) -> str:
    """The RGA crop-and-scale filter as it starts in the graph (NV12 out, fixed size)."""
    return (
        f"{RGA_FILTER_TARGET}=w={output_width}:h={output_height}"
        f":cw={_width_option(pixels)}:ch=cw*{output_height}/{output_width}"
        f":cx={_left_option(pixels)}:cy={_top_option(pixels)}:format=nv12"
    )


def rga_window_commands(previous: CropPixels | None, new: CropPixels) -> list[str]:
    """The zmq commands that move the RGA's crop from `previous` to `new` (all when unknown).

    The same shape as the laptop's (`crop_commands.window_commands`): the
    width first, and only what changed.
    """
    commands = []
    if previous is None or previous.width != new.width:
        commands.append(f"{RGA_FILTER_TARGET} cw {_width_option(new)}")
    if previous is None or previous.centre_x != new.centre_x:
        commands.append(f"{RGA_FILTER_TARGET} cx {_left_option(new)}")
    if previous is None or previous.centre_y != new.centre_y:
        commands.append(f"{RGA_FILTER_TARGET} cy {_top_option(new)}")
    return commands


def snapshot_filters(snapshot_width: int, snapshot_fps: int) -> str:
    """The snapshot branch: one frame a second, shrunk by the RGA, copied out, made small."""
    return (
        f"fps={snapshot_fps},"
        f"vpp_rkrga=w={RGA_SNAPSHOT_WIDTH}:h=trunc({RGA_SNAPSHOT_WIDTH}*ih/iw/2)*2:format=nv12,"
        f"hwdownload,format=nv12,scale={snapshot_width}:-2,format=yuvj420p"
    )


# The live branch's end when the encoder cannot take hardware frames (x264, forced by hand).
DOWNLOAD_FOR_SOFTWARE_ENCODER = "hwdownload,format=nv12"


# --- checking the ffmpeg ----------------------------------------------------------------------


def runtime_crop_problem_in(help_text: str) -> str:
    """Why this ffmpeg cannot move the RGA's crop live ("" when it can), from its filter help.

    `ffmpeg -h filter=vpp_rkrga` marks options that can change while running
    with a `T` in their flags column; the patch gives `cw`, `cx`, `cy` that.
    """
    if "vpp_rkrga" not in help_text or "Unknown filter" in help_text:
        return "this ffmpeg has no Rockchip RGA filter (vpp_rkrga): install the node's ffmpeg"
    for line in help_text.splitlines():
        words = line.split()
        if len(words) >= 3 and words[0] == "cw":
            flags = words[2]
            if "T" in flags:
                return ""
            break
    return (
        "this ffmpeg cannot move the RGA crop while running (unpatched ffmpeg-rockchip): "
        "install the node's ffmpeg"
    )


def runtime_crop_problem(ffmpeg_path: str) -> str:
    """Ask `ffmpeg_path` whether it can move the RGA's crop live; "" when it can."""
    try:
        finished = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", "filter=vpp_rkrga"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=HELP_TIMEOUT_SECONDS,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"could not ask ffmpeg about its RGA filter: {error}"
    text = finished.stdout.decode("utf-8", errors="replace")
    return runtime_crop_problem_in(text + finished.stderr.decode("utf-8", errors="replace"))


def missing_device_nodes(device_nodes: tuple[Path, ...] = ROCKCHIP_DEVICE_NODES) -> list[str]:
    """The Rockchip device nodes this service cannot open (absent, or no permission)."""
    return [str(node) for node in device_nodes if not os.access(node, os.R_OK | os.W_OK)]
