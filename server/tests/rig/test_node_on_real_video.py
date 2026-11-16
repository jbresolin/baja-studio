"""The whole camera node on its real ffmpeg pipeline: the studio moves the window, it follows.

The other node tests run on `FakeCameraPipeline`, and the pipeline's own rig
tests (`test_node_video_real.py`) drive `CameraPipeline` directly. This one
joins the two: the node app, as on a laptop, with a camera in `node` mode
whose device is a synthetic 4K camera (`lavfi:quadrants`: four flat greys, so
the brightness of what comes out tells where the window is). Its picture goes
to a local UDP port, read back by a `PictureProbe` (a second ffmpeg).

What it checks, in the order the studio would use it:

1. the node starts the real pipeline and the camera says `ok`;
2. a `PUT /crop` from the studio moves what the picture shows;
3. the snapshot route serves the pipeline's whole-frame JPEG;
4. the node's page shows the pipeline's figures and ffmpeg's last lines.
"""

from pathlib import Path
from typing import Any

import pytest

from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.probe_reader import PictureProbe
from baja_studio.rig.layout import free_udp_port
from tests.node.node_harness import RunningNode, node_mode_camera, running_node
from tests.rig.node_video_tools import ffmpeg_path, needs_node_ffmpeg, wait_until

pytestmark = [pytest.mark.rig, needs_node_ffmpeg]

# The grey a probe reads for each quarter of the quadrants picture (as in
# test_node_video_real.py: 0, 85, 170, 255 drawn, moved a little by the encoder).
TOP_LEFT_GREY = 0
BOTTOM_RIGHT_GREY = 255
GREY_TOLERANCE = 25


def synthetic_camera(port: int) -> dict[str, Any]:
    """A `node` mode camera on the synthetic quadrants camera, sending to a local UDP port."""
    return node_mode_camera(
        "cam1",
        video={
            "device": "lavfi:quadrants",
            "device_name": "Grey quadrants (synthetic)",
            "input_width": 3840,
            "input_height": 2160,
            "input_fps": 30,
            "output_url": f"udp://127.0.0.1:{port}?pkt_size=1316",
            "encoder": "libx264",
        },
    )


def grey_now(probe: PictureProbe) -> float:
    latest = probe.latest()
    return -1.0 if latest is None else latest.mean()


def page_details(node: RunningNode) -> dict[str, Any]:
    """What the node's page shows for cam1 (its pipeline figures, ffmpeg's lines)."""
    details: dict[str, Any] = node.module.cameras.details("cam1")  # type: ignore[union-attr]
    return details


def test_the_studio_moves_the_window_of_a_real_pipeline_through_the_node(
    tmp_path: Path,
) -> None:
    port = free_udp_port()
    probe = PictureProbe(ffmpeg_path(), f"udp://127.0.0.1:{port}")
    try:
        with running_node(
            tmp_path, [synthetic_camera(port)], real_video=(CameraPipeline, ffmpeg_path())
        ) as node:
            # 1. The real pipeline runs, and the camera is healthy.
            wait_until(lambda: node.state("cameras.cam1.status") == "ok", "cam1 ok")
            wait_until(lambda: probe.latest() is not None, "the probe seeing pictures")

            # 2. The studio puts the window on the top-left quarter, then the bottom-right.
            node.http.put("/cameras/cam1/crop", json={"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})
            wait_until(
                lambda: abs(grey_now(probe) - TOP_LEFT_GREY) <= GREY_TOLERANCE,
                "the picture showing the top-left quarter",
            )
            node.http.put("/cameras/cam1/crop", json={"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5})
            wait_until(
                lambda: abs(grey_now(probe) - BOTTOM_RIGHT_GREY) <= GREY_TOLERANCE,
                "the picture showing the bottom-right quarter",
            )

            # 3. The snapshot is the pipeline's JPEG of the whole frame.
            wait_until(
                lambda: node.http.get("/cameras/cam1/snapshot").status_code == 200,
                "a snapshot from the real pipeline",
            )
            snapshot = node.http.get("/cameras/cam1/snapshot")
            assert snapshot.headers["content-type"] == "image/jpeg"
            assert snapshot.content.startswith(b"\xff\xd8")

            # 4. The page shows the pipeline's figures and ffmpeg's last lines. (A UDP
            # URL has no password; that secrets stay out is checked with a Feeds URL in
            # tests/node/test_video_modes.py.)
            details = page_details(node)
            assert details["pipeline"]["state"] == "running"
            assert details["pipeline"]["encoder"] == "libx264"
            assert details["ffmpeg_log"], "the page shows none of ffmpeg's lines"
    finally:
        probe.close()
