"""The laptop benchmark: `python -m baja_studio.node.video.bench` (brief §3, bench sheet step 1).

What it does, in order, printing as it goes:

1. lists the DirectShow cameras and their modes (`--raw`: ffmpeg's own words too);
2. tries every H.264 encoder (a one-second test encode each) and says which work;
3. runs **one camera** for `--seconds` (60) through the real pipeline to a
   local sink, playing operator on it (`bench_pass.py`), then **two cameras**
   together if there are two;
4. on a real camera, tries the hardware MJPEG decoders (QuickSync, NVIDIA)
   for 20 s each, to see whether they work and how much CPU they save
   (`--skip-decoders` to leave this out).

Per camera it prints frames in and out per second, dropped frames, the
encoder, the bitrate, the window apply time (window sent → reached a frame)
and the move latency (window sent → the probe saw the picture change),
and the laptop's CPU use for each pass.

Without cameras (or with `--synthetic`) it runs the synthetic 4K test card
instead, which measures the laptop's encode path without the camera's.
"""

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from baja_studio.core.system_services import InstallLayout
from baja_studio.node.video.bench_pass import CameraResult, PassResult, run_pass
from baja_studio.node.video.capture_devices import (
    SYNTHETIC_DEVICES,
    CaptureDevice,
    CaptureMode,
    capture_modes,
    list_capture_devices,
    raw_device_listing,
    raw_mode_listing,
)
from baja_studio.node.video.encoders import AUTO_ORDER, PROFILES, probe_encoder
from baja_studio.node.video.probe_reader import summarise_ms
from baja_studio.node.video.spec import SOFTWARE_DECODER, PipelineSpec
from baja_studio.node.video.synthetic_sources import is_synthetic
from baja_studio.playout.tools import FFMPEG, resolve_tool

HARDWARE_DECODERS = ("mjpeg_qsv", "mjpeg_cuvid")
DECODER_PASS_SECONDS = 20.0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """The command line (see `--help`)."""
    parser = argparse.ArgumentParser(
        prog="python -m baja_studio.node.video.bench",
        description="Measure the camera node's video pipeline on this laptop.",
    )
    parser.add_argument("--ffmpeg", default="", help="ffmpeg to use (default: bundled, else PATH)")
    parser.add_argument("--seconds", type=float, default=60.0, help="length of each pass")
    parser.add_argument("--encoder", default="auto", help="auto, h264_nvenc, h264_qsv, ...")
    parser.add_argument("--decoder", default=SOFTWARE_DECODER, help="MJPEG decoder for the passes")
    parser.add_argument("--mode", default="", help="camera mode, e.g. 3840x2160@30 (default: best)")
    parser.add_argument("--cameras", type=int, default=2, help="at most this many cameras")
    parser.add_argument("--synthetic", action="store_true", help="use the synthetic test card")
    parser.add_argument("--raw", action="store_true", help="also print ffmpeg's device listings")
    parser.add_argument("--skip-decoders", action="store_true", help="skip the decoder passes")
    parser.add_argument("--list", action="store_true", help="only list cameras and encoders")
    return parser.parse_args(argv)


def resolve_ffmpeg(configured: str) -> str:
    """The ffmpeg to run: the one given, else the installer's copy, else PATH.

    Run from a terminal, the installer's environment variable is not set; the
    installed Python is `<program>\\python\\python.exe`, so the program folder
    is also found from it (`<program>\\ffmpeg\\ffmpeg.exe`, as the studio has).
    """
    layout = InstallLayout.from_environment() or InstallLayout(Path(sys.executable).parents[1])
    return resolve_tool(FFMPEG, configured, layout).command


def parse_mode(text: str) -> tuple[int, int, float] | None:
    """`3840x2160@30` -> (3840, 2160, 30.0); None if empty."""
    if not text:
        return None
    size, _, rate = text.partition("@")
    width, _, height = size.partition("x")
    return int(width), int(height), float(rate or 30)


def best_mode(modes: list[CaptureMode]) -> tuple[int, int, float]:
    """The biggest MJPEG mode at 25 fps or more (else the first mode, else 4K30)."""
    fast_mjpeg = [mode for mode in modes if mode.is_mjpeg and mode.max_fps >= 25]
    chosen = fast_mjpeg[0] if fast_mjpeg else (modes[0] if modes else None)
    if chosen is None:
        return 3840, 2160, 30.0
    return chosen.width, chosen.height, min(chosen.max_fps, 30.0)


def list_cameras(ffmpeg: str, show_raw: bool) -> list[tuple[CaptureDevice, list[CaptureMode]]]:
    """Print and return the cameras with their modes."""
    if show_raw:
        print(raw_device_listing(ffmpeg))
    cameras = []
    for device in list_capture_devices(ffmpeg):
        modes = capture_modes(device.device, ffmpeg)
        print(f"camera: {device.name}  ({device.device})")
        for mode in modes:
            print(f"    {mode.describe()}")
        if show_raw and not is_synthetic(device.device):
            print(raw_mode_listing(device.device, ffmpeg))
        cameras.append((device, modes))
    if not cameras:
        print("no cameras found")
    return cameras


def list_encoders(ffmpeg: str) -> None:
    """Print which encoders pass the test encode."""
    for name in AUTO_ORDER:
        result = probe_encoder(ffmpeg, name)
        verdict = "works" if result.works else f"does not work: {result.message}"
        print(f"encoder {PROFILES[name].label} ({name}): {verdict}")


def print_camera(result: CameraResult) -> None:
    """One camera's lines of the report."""
    status = result.status
    print(f"  camera {result.camera}")
    if status is None:
        print("    no status (did not start)")
        return
    print(f"    state {status.state}  {status.reason}")
    print(
        f"    fps in {status.fps_in:.1f}, out {status.fps_out:.1f}, "
        f"dropped {status.dropped_frames}, restarts {status.restarts}"
    )
    print(f"    encoder {status.encoder}, bitrate {status.bitrate_kbps:.0f} kb/s")
    apply = "not measured" if status.window_apply_ms is None else f"{status.window_apply_ms:.0f} ms"
    print(f"    window apply (median) {apply}")
    print(
        f"    move latency {summarise_ms(result.move_latencies_ms)}"
        + (f", {result.moves_not_seen} moves not seen" if result.moves_not_seen else "")
    )
    if status.state != "running":
        for line in result.log_tail[-8:]:
            print(f"    | {line}")


def print_pass(title: str, result: PassResult) -> None:
    """A pass's report."""
    print(f"\n=== {title}: laptop CPU {result.cpu_summary()}")
    for camera in result.cameras:
        print_camera(camera)


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark; see the module docstring."""
    arguments = parse_arguments(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logger = logging.getLogger("bench")
    ffmpeg = resolve_ffmpeg(arguments.ffmpeg)
    print(f"ffmpeg: {ffmpeg}")
    cameras = list_cameras(ffmpeg, arguments.raw)
    list_encoders(ffmpeg)
    if arguments.list:
        return 0
    if arguments.synthetic or not cameras:
        no_modes: list[CaptureMode] = []
        cameras = [(SYNTHETIC_DEVICES[0], no_modes)] * 2
    chosen = cameras[: max(1, arguments.cameras)]
    specs = []
    for device, modes in chosen:
        width, height, fps = parse_mode(arguments.mode) or best_mode(modes)
        specs.append(
            PipelineSpec(
                device=device.device,
                input_width=width,
                input_height=height,
                input_fps=fps,
                output_url="",  # each pass sends to its own local sink
                encoder=arguments.encoder,
                decoder=arguments.decoder,
                ffmpeg_path=ffmpeg,
                label=device.name,
            )
        )
    print_pass("1 camera", run_pass(specs[:1], arguments.seconds, ffmpeg, logger))
    if len(specs) > 1:
        print_pass(f"{len(specs)} cameras", run_pass(specs, arguments.seconds, ffmpeg, logger))
    if not arguments.skip_decoders and not is_synthetic(specs[0].device):
        for decoder in HARDWARE_DECODERS:
            spec = replace(specs[0], decoder=decoder)
            result = run_pass([spec], DECODER_PASS_SECONDS, ffmpeg, logger)
            print_pass(f"1 camera, decoder {decoder}", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
