"""Command line: `python -m baja_studio.node [--port 8891] [--data-dir PATH] [--fake-video]`.

The node installer's service runs it with no options: the port comes from
the node's settings (8891 by default), the data folder from `BAJA_NODE_DATA`.
`--fake-video` serves fake pipelines and capture devices instead of ffmpeg
(the rehearsal rig, CI's conformance run); `--fake-cameras N` also creates N
`node`-mode cameras (replacing the saved ones), so the node has something
to serve; `--token` sets the token (saved, as from the page: the rig and CI).
"""

import argparse
import logging
from pathlib import Path

import uvicorn

from baja_studio.node.app import create_node_app, default_node_data_directory
from baja_studio.node.parts import fake_parts, real_parts
from baja_studio.node.settings import NodeSettings
from baja_studio.node.test_cameras import fake_camera_settings

logger = logging.getLogger("baja_studio.node")


def main(argv: list[str] | None = None) -> None:
    """Parse the options and serve the node until the service manager stops it."""
    parser = argparse.ArgumentParser(prog="baja_studio.node", description="Run a camera node.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument("--port", type=int, default=None, help="port (default: the settings')")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"folder for the database and logs (default: {default_node_data_directory()})",
    )
    parser.add_argument("--fake-video", action="store_true", help="fake pipelines and devices")
    parser.add_argument(
        "--fake-cameras", type=int, default=0, help="with --fake-video: serve N node-mode cameras"
    )
    parser.add_argument("--token", default=None, help="set the agent token (saved)")
    parser.add_argument("--agent-id", default=None, help="the agent id for this run")
    options = parser.parse_args(argv)
    parts = fake_parts() if options.fake_video else real_parts()
    app = create_node_app(parts, data_directory=options.data_dir, token_override=options.token)
    settings_store = app.state.services.settings
    patch: dict[str, object] = {}
    if options.fake_video and options.fake_cameras:
        patch["cameras"] = [
            camera.model_dump() for camera in fake_camera_settings(options.fake_cameras)
        ]
    if options.agent_id:
        patch["agent_id"] = options.agent_id
    if patch:
        settings_store.update("node", patch)
    settings = settings_store.get("node")
    assert isinstance(settings, NodeSettings)
    port = options.port or settings.port
    # log_config=None keeps uvicorn from replacing the studio's logging setup.
    uvicorn.run(app, host=options.host, port=port, log_config=None)


if __name__ == "__main__":
    main()
