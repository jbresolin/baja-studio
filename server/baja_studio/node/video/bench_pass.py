"""One benchmark pass: N cameras through the real pipeline for a while, measured.

Each camera gets a real `CameraPipeline` sending to a local UDP port (no
studio needed) and a `PictureProbe` watching that port. While they run, a
mover plays operator on every camera at the same time:

- for `PAN_SECONDS` it pans and zooms smoothly, 30 window updates a second
  (what the joystick does, and what loads the window path);
- then it holds still for `SETTLE_SECONDS`, jumps the window between two
  far-apart corners at 2x, and times how long until the probe sees the
  picture change (`MOVE_THRESHOLD`): the move latency, encode and decode
  included, network excluded.

The laptop's CPU use is read every second; the pipelines' own numbers
(frames in and out, drops, bitrate, encoder, the zmq apply time) are read
at the end.
"""

import logging
import math
import statistics
import threading
import time
from dataclasses import dataclass, field, replace

from baja_studio.core.clock import SystemClock
from baja_studio.node.video.cpu_usage import SystemCpu
from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.pipeline_parts import free_loopback_port
from baja_studio.node.video.probe_reader import PictureProbe, first_changed_frame
from baja_studio.node.video.spec import PipelineSpec, PipelineState, PipelineStatus, SourceWindow

UPDATES_PER_SECOND = 30
PAN_SECONDS = 4.0
SETTLE_SECONDS = 1.0
MOVE_WAIT_SECONDS = 1.5
# Average grey-level change (0..255) that counts as "the picture moved".
MOVE_THRESHOLD = 8.0
STARTUP_WAIT_SECONDS = 25.0
# Two windows at 2x, far apart: top-left and bottom-right quarters.
JUMP_WINDOWS = (SourceWindow(0.0, 0.0, 0.5, 0.5), SourceWindow(0.5, 0.5, 0.5, 0.5))


@dataclass
class CameraResult:
    """What one camera did during a pass."""

    camera: str
    status: PipelineStatus | None = None
    move_latencies_ms: list[float] = field(default_factory=list)
    moves_not_seen: int = 0
    log_tail: list[str] = field(default_factory=list)


@dataclass
class PassResult:
    """A whole pass: each camera's result and the laptop's CPU use."""

    cameras: list[CameraResult]
    cpu_percent: list[float]

    def cpu_summary(self) -> str:
        """`mean 41 %, max 55 %` (or "not available")."""
        if not self.cpu_percent:
            return "not available"
        return f"mean {statistics.mean(self.cpu_percent):.0f} %, max {max(self.cpu_percent):.0f} %"


def udp_sink(port: int) -> str:
    """Where a bench pipeline sends: a local UDP port (MPEG-TS packets, SRT-sized)."""
    return f"udp://127.0.0.1:{port}?pkt_size=1316"


def panning_window(phase: float) -> SourceWindow:
    """A window that wanders smoothly over the frame and zooms between 1x and 2x."""
    width = 0.75 + 0.25 * math.sin(phase * 0.7)
    x = (1 - width) * (0.5 + 0.5 * math.sin(phase))
    y = (1 - width) * (0.5 + 0.5 * math.cos(phase * 1.3))
    return SourceWindow(x, y, width, width)


class _Camera:
    def __init__(self, spec: PipelineSpec, ffmpeg_path: str, logger: logging.Logger) -> None:
        port = free_loopback_port()
        self.spec = replace(spec, output_url=udp_sink(port))
        self.pipeline = CameraPipeline(self.spec, SystemClock(), logger)
        self.probe = PictureProbe(ffmpeg_path, f"udp://127.0.0.1:{port}")
        self.result = CameraResult(spec.camera_name)


def _wait_running(cameras: list[_Camera]) -> bool:
    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if all(camera.pipeline.status().state == PipelineState.RUNNING for camera in cameras):
            # Let the probes lock on to a keyframe.
            time.sleep(2.0)
            return True
        time.sleep(0.2)
    return False


def _jump(cameras: list[_Camera], target: SourceWindow) -> None:
    references = [camera.probe.latest() for camera in cameras]
    sent_at = time.monotonic()
    for camera in cameras:
        camera.pipeline.set_window(target)
    time.sleep(MOVE_WAIT_SECONDS)
    for camera, reference in zip(cameras, references, strict=True):
        changed = (
            first_changed_frame(camera.probe.frames_after(sent_at), reference, MOVE_THRESHOLD)
            if reference is not None
            else None
        )
        if changed is None:
            camera.result.moves_not_seen += 1
        else:
            camera.result.move_latencies_ms.append((changed.read_at - sent_at) * 1000)


def _operate(cameras: list[_Camera], seconds: float) -> None:
    """Pan, settle, jump; again until `seconds` have passed."""
    end = time.monotonic() + seconds
    phase = 0.0
    jumps = 0
    while time.monotonic() < end:
        pan_end = min(end, time.monotonic() + PAN_SECONDS)
        while time.monotonic() < pan_end:
            phase += 1 / UPDATES_PER_SECOND
            for camera in cameras:
                camera.pipeline.set_window(panning_window(phase))
            time.sleep(1 / UPDATES_PER_SECOND)
        for camera in cameras:
            camera.pipeline.set_window(JUMP_WINDOWS[jumps % 2])
        time.sleep(SETTLE_SECONDS)
        jumps += 1
        _jump(cameras, JUMP_WINDOWS[jumps % 2])


def run_pass(
    specs: list[PipelineSpec], seconds: float, ffmpeg_path: str, logger: logging.Logger
) -> PassResult:
    """Run `specs` together for `seconds` of operating (after they start), measured."""
    cameras = [_Camera(spec, ffmpeg_path, logger) for spec in specs]
    cpu = SystemCpu()
    cpu_readings: list[float] = []
    stop_sampling = threading.Event()

    def sample_cpu() -> None:
        while not stop_sampling.wait(1.0):
            reading = cpu.percent()
            if reading is not None:
                cpu_readings.append(reading)

    sampler = threading.Thread(target=sample_cpu, name="bench-cpu", daemon=True)
    try:
        for camera in cameras:
            camera.pipeline.start()
        if _wait_running(cameras):
            cpu.percent()  # start counting from now
            sampler.start()
            _operate(cameras, seconds)
        for camera in cameras:
            camera.result.status = camera.pipeline.status()
            camera.result.log_tail = camera.pipeline.log_tail()
    finally:
        stop_sampling.set()
        if sampler.is_alive():
            sampler.join()
        for camera in cameras:
            camera.pipeline.stop()
            camera.probe.close()
    return PassResult([camera.result for camera in cameras], cpu_readings)
