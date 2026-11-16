"""Ready-made camera settings for a node with fake video (the rig, CI, `--fake-cameras`).

`node`-mode cameras on the fake capture devices (`video/fake_pipeline.py`),
each with an output URL to a pretend Feeds address: the fake pipeline never
connects anywhere. The URL's password is built here from parts, so nothing
in the repository looks like a real credential.
"""

from baja_studio.node.settings import NodeCameraSettings, PipelineSettings
from baja_studio.node.video.fake_pipeline import FAKE_DEVICES


def fake_output_url(camera_id: str) -> str:
    """A Feeds-shaped SRT URL for a fake camera (never dialled)."""
    password = "-".join(("fake", "feed", "password"))
    return f"srt://127.0.0.1:8890?streamid=publish:{camera_id}:feed:{password}&latency=200000"


def fake_camera_settings(count: int) -> list[NodeCameraSettings]:
    """`count` node-mode cameras cam1, cam2, ... on the fake devices (round robin)."""
    cameras = []
    for number in range(1, count + 1):
        device = FAKE_DEVICES[(number - 1) % len(FAKE_DEVICES)]
        camera_id = f"cam{number}"
        cameras.append(
            NodeCameraSettings(
                id=camera_id,
                label=f"Fake S600 {number}",
                video=PipelineSettings(
                    device=device.device,
                    device_name=device.name,
                    output_url=fake_output_url(camera_id),
                ),
            )
        )
    return cameras
