"""Tests for what a camera node is made of where it runs (node/parts.py): here, which ffmpeg.

On the NanoPi the Linux installer puts its own ffmpeg build (ffmpeg-rockchip
with the node's patch) in `<program>/ffmpeg/bin/ffmpeg` (ADR 0024). These
tests build that folder in a temporary place and check the node finds it,
and that a path typed in the settings still wins.
"""

import sys
from pathlib import Path

import pytest

from baja_studio.core.system_services import INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE, InstallLayout
from baja_studio.node.parts import bundled_ffmpeg, linux_bundled_ffmpeg
from baja_studio.node.settings import NodeSettings


def make_linux_install(program: Path) -> Path:
    """A program folder with an "ffmpeg" where the Linux installer puts it."""
    ffmpeg = program / "ffmpeg" / "bin" / "ffmpeg"
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_text("#!/bin/sh\n")
    return ffmpeg


def test_the_linux_installer_s_ffmpeg_is_found_in_its_program_folder(tmp_path: Path) -> None:
    ffmpeg = make_linux_install(tmp_path)
    assert linux_bundled_ffmpeg(InstallLayout(tmp_path)) == str(ffmpeg)


def test_without_an_install_there_is_no_bundled_linux_ffmpeg(tmp_path: Path) -> None:
    assert linux_bundled_ffmpeg(None) is None
    assert linux_bundled_ffmpeg(InstallLayout(tmp_path)) is None


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux installer's layout")
def test_the_node_runs_the_bundled_ffmpeg_unless_the_settings_name_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = make_linux_install(tmp_path)
    # The service's environment says where the program is installed.
    monkeypatch.setenv(INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE, str(tmp_path))
    assert bundled_ffmpeg(NodeSettings()) == str(ffmpeg)
    # A path typed on the node's page wins over the bundled one.
    assert bundled_ffmpeg(NodeSettings(ffmpeg_path="/usr/local/bin/ffmpeg")) == (
        "/usr/local/bin/ffmpeg"
    )
