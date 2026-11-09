"""Tests for choosing which ffprobe and ffmpeg playout runs (playout/tools.py).

An installed studio has its own copies in `<program folder>\\ffmpeg\\`. These
tests build that folder in a temporary directory and replace PATH lookups
with a small dictionary, so nothing depends on what this computer has.
"""

from pathlib import Path

import pytest

from baja_studio.core.system_services import INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE, InstallLayout
from baja_studio.playout.config import ToolsSettings
from baja_studio.playout.tools import ToolSource, resolve_tool, resolve_tools

# What `shutil.which` would answer on a PC with ffmpeg installed system-wide.
ON_PATH = {"ffprobe": "C:\\ffmpeg\\bin\\ffprobe.exe", "ffmpeg": "C:\\ffmpeg\\bin\\ffmpeg.exe"}


def which_on_path(command: str) -> str | None:
    return ON_PATH.get(command)


def which_nothing(command: str) -> str | None:
    return None


def installed_with_ffmpeg(program_directory: Path) -> InstallLayout:
    """A program folder as the installer leaves it, with both tools bundled."""
    bundled = program_directory / "ffmpeg"
    bundled.mkdir(parents=True)
    for name in ("ffprobe", "ffmpeg"):
        (bundled / f"{name}.exe").write_bytes(b"")
    return InstallLayout(program_directory)


def test_the_default_name_uses_the_bundled_copy_when_installed(tmp_path: Path) -> None:
    layout = installed_with_ffmpeg(tmp_path)

    # Settings still say just "ffprobe" (the default, or a v0.1.0 install).
    tool = resolve_tool("ffprobe", "ffprobe", layout, which_on_path)

    # The installer's copy wins over the one on PATH.
    assert tool.source == ToolSource.BUNDLED
    assert tool.command == str(tmp_path / "ffmpeg" / "ffprobe.exe")
    assert tool.found_at == tool.command


def test_the_default_name_falls_back_to_path_without_a_bundled_copy(tmp_path: Path) -> None:
    # Installed, but the ffmpeg folder is missing (an older installer).
    layout = InstallLayout(tmp_path)

    tool = resolve_tool("ffmpeg", "ffmpeg", layout, which_on_path)

    assert tool.source == ToolSource.PATH
    assert tool.command == "ffmpeg"
    assert tool.found_at == ON_PATH["ffmpeg"]


def test_from_a_checkout_the_default_name_is_looked_up_on_path() -> None:
    # No install folder at all: a developer running from the repository.
    tool = resolve_tool("ffprobe", "ffprobe", None, which_on_path)

    assert tool.source == ToolSource.PATH
    assert tool.found_at == ON_PATH["ffprobe"]


def test_a_path_typed_in_the_settings_always_wins(tmp_path: Path) -> None:
    layout = installed_with_ffmpeg(tmp_path / "program")
    typed = str(tmp_path / "tools" / "ffprobe.exe")

    tool = resolve_tool("ffprobe", typed, layout, lambda command: command)

    # Not the bundled copy: the operator chose another one on purpose.
    assert tool.source == ToolSource.SETTINGS
    assert tool.command == typed


def test_a_tool_found_nowhere_says_so() -> None:
    tool = resolve_tool("ffprobe", "ffprobe", None, which_nothing)

    assert tool.found_at is None
    assert tool.describe() == "ffprobe: PATH (not found)"


def test_both_tools_use_the_install_folder_the_installer_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The installer gives the service its program folder in the environment.
    installed_with_ffmpeg(tmp_path)
    monkeypatch.setenv(INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE, str(tmp_path))

    ffprobe, ffmpeg = resolve_tools(ToolsSettings(), which=which_nothing)

    assert ffprobe.source == ffmpeg.source == ToolSource.BUNDLED
    assert ffmpeg.describe() == f"ffmpeg: bundled ({tmp_path / 'ffmpeg' / 'ffmpeg.exe'})"
