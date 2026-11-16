"""What a `CameraPipeline` uses to reach the outside world, so tests can swap each piece.

The real parts start ffmpeg, run test encodes, and send windows over ZeroMQ
on loopback TCP. A unit test gives the pipeline fakes instead (a scripted
ffmpeg run, a prober that answers at once, a sender that records windows)
and drives it on the fake clock; the rig tests use the real parts.
"""

import logging
import shutil
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import zmq

from baja_studio.core.clock import Clock
from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.encoders import EncoderProber, probe_encoder
from baja_studio.node.video.ffmpeg_command import ZMQ_HOST
from baja_studio.node.video.ffmpeg_process import Launcher, launch_ffmpeg
from baja_studio.node.video.window_sender import WindowSender

Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


class WindowSending(Protocol):
    """What the pipeline needs from a window sender (`window_sender.WindowSender`)."""

    def start(self) -> None:
        """Start sending."""

    def stop(self) -> None:
        """Stop sending and release everything; returns when done."""

    def offer(self, window: CropPixels) -> None:
        """Send this window next; never blocks."""

    def median_apply_ms(self) -> float | None:
        """How long recent windows took to reach a frame."""


# (zmq port of this run, the window ffmpeg starts with, logger) -> a sender.
SenderFactory = Callable[[int, CropPixels, Logger], WindowSending]


def free_loopback_port() -> int:
    """A TCP port on 127.0.0.1 that nobody listens on right now (for the zmq filter).

    There is a short window before ffmpeg binds it; if someone takes it
    meanwhile, ffmpeg says so and the next run gets another port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((ZMQ_HOST, 0))
        return int(probe.getsockname()[1])


def ffmpeg_is_installed(ffmpeg_path: str) -> bool:
    """True if `ffmpeg_path` (a path, or a name on PATH) can be run."""
    return shutil.which(ffmpeg_path) is not None


class ZmqSenders:
    """Makes real window senders, all sharing one ZeroMQ context (one per pipeline)."""

    def __init__(self, clock: Clock) -> None:
        """A fresh context; `close` ends it once every sender has stopped."""
        self._clock = clock
        self._context: zmq.Context[zmq.Socket[bytes]] = zmq.Context()

    def __call__(self, port: int, starting_window: CropPixels, logger: Logger) -> WindowSending:
        """A sender for the ffmpeg whose zmq filter listens on `port`."""
        return WindowSender(
            self._context,
            f"tcp://{ZMQ_HOST}:{port}",
            starting_window,
            self._clock,
            logger,
            name=f"node-video-sender.{port}",
        )

    def close(self) -> None:
        """End the context (every sender must be stopped first, or this would wait for them)."""
        self._context.term()


@dataclass(frozen=True)
class PipelineParts:
    """The pipeline's collaborators. Leave a field out to get the real one."""

    launch: Launcher = launch_ffmpeg
    probe_encoder: EncoderProber = probe_encoder
    # None: real ZeroMQ senders (`ZmqSenders`), made and closed by the pipeline.
    make_sender: SenderFactory | None = None
    free_port: Callable[[], int] = free_loopback_port
    ffmpeg_exists: Callable[[str], bool] = ffmpeg_is_installed
