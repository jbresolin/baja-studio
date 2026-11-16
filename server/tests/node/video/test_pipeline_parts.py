"""Tests for the pipeline's real collaborators (node/video/pipeline_parts.py).

The parts that open sockets (a free port, real ZeroMQ senders) are tested
with the real ffmpeg in `tests/rig/test_node_video_real.py`.
"""

import sys
from pathlib import Path

from baja_studio.node.video.pipeline_parts import (
    PipelineParts,
    ffmpeg_is_installed,
    free_loopback_port,
)


def test_ffmpeg_is_found_by_path_or_name_or_not_at_all(tmp_path: Path) -> None:
    # The running Python stands in for "a program that exists".
    assert ffmpeg_is_installed(sys.executable)
    assert not ffmpeg_is_installed(str(tmp_path / "ffmpeg.exe"))


def test_by_default_every_part_is_the_real_one() -> None:
    parts = PipelineParts()
    assert parts.make_sender is None  # the pipeline makes ZeroMQ senders itself
    assert parts.free_port is free_loopback_port
