"""The pinned node ffmpeg has what the camera node needs (installer/ffmpeg-node/pin.json, ADR 0023).

CI downloads the pinned build (the Windows one on the Windows runner, the
Linux build of the same version on Linux), checks its SHA-256, and puts its
path in `BAJA_STUDIO_TEST_NODE_FFMPEG`. These tests then ask that very
binary what it has. Listing an encoder needs no GPU, so NVENC, QuickSync and
AMF are checked here even though CI cannot run them; whether they *work* on
a laptop is what the bench tool tells (bench sheet step 1).

Without the variable (a developer's machine) they are skipped: they are
about the pinned build only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from baja_studio.node.video.encoders import PROFILES
from tests.rig.node_video_tools import NODE_FFMPEG_VARIABLE, missing_features

pytestmark = [
    pytest.mark.rig,
    pytest.mark.skipif(
        not os.environ.get(NODE_FFMPEG_VARIABLE), reason=f"{NODE_FFMPEG_VARIABLE} is not set"
    ),
]

PIN = json.loads(
    (Path(__file__).parents[3] / "installer" / "ffmpeg-node" / "pin.json").read_text("utf-8")
)


def pinned() -> str:
    return os.environ[NODE_FFMPEG_VARIABLE]


def ask(*arguments: str) -> str:
    """What the pinned ffmpeg prints for `arguments` (stdout and stderr together)."""
    finished = subprocess.run(
        [pinned(), "-hide_banner", *arguments], capture_output=True, text=True, check=False
    )
    return finished.stdout + finished.stderr


def test_it_is_the_pinned_version() -> None:
    # `ffmpeg -version` starts with "ffmpeg version n8.1.3-27-g8dca1586f4-20261031 ...".
    first_line = subprocess.run(
        [pinned(), "-version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    assert f"ffmpeg version {PIN['version']}" in first_line


def test_it_has_the_zmq_filter_srt_and_x264() -> None:
    assert missing_features(pinned()) == []


def test_it_has_every_filter_the_pipeline_graph_uses() -> None:
    filters = ask("-filters").split()
    for name in ("zmq", "crop", "null", "scale", "setsar", "format", "split", "fps"):
        assert name in filters, name


def test_it_lists_every_encoder_and_decoder_the_node_may_use() -> None:
    encoders = ask("-encoders").split()
    for name in (*PROFILES, "mjpeg"):
        assert name in encoders, name
    decoders = ask("-decoders").split()
    for name in ("mjpeg", "mjpeg_qsv", "mjpeg_cuvid"):
        assert name in decoders, name


@pytest.mark.parametrize("encoder", sorted(PROFILES))
def test_every_option_the_pipeline_gives_an_encoder_exists(encoder: str) -> None:
    # A misspelt option makes ffmpeg refuse to start. `-h encoder=<name>` lists
    # the encoder's own options; every one the pipeline passes must be there.
    described = ask("-h", f"encoder={encoder}")
    profile = PROFILES[encoder]
    options = list(profile.options[::2])
    if profile.keyframe_option:
        options.append(profile.keyframe_option)
    for option in options:
        assert f"{option} " in described, f"{encoder} has no option {option}"


@pytest.mark.skipif(sys.platform != "win32", reason="DirectShow exists on Windows only")
def test_on_windows_it_captures_with_directshow() -> None:
    assert "dshow" in ask("-devices").split()


def test_the_capture_options_the_pipeline_gives_exist() -> None:
    # DirectShow only exists on Windows, but these two are ffmpeg's general
    # input options, listed by `-h full` on every system: the real-time
    # buffer size, and the `nobuffer` flag (drop frames read while starting).
    described = ask("-h", "full")
    for option in ("-rtbufsize ", "-analyzeduration ", "nobuffer"):
        assert option in described, option
    # QuickSync's MJPEG decoder takes the in-flight depth the pipeline sets.
    assert "-async_depth " in ask("-h", "decoder=mjpeg_qsv")
