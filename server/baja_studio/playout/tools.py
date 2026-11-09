"""Which ffprobe and ffmpeg playout runs: the installer's copies, a configured path, or PATH.

The installer puts both in `<program folder>\\ffmpeg\\` (ADR 0010). The
settings keep the bare names `ffprobe` and `ffmpeg` by default; while they
do, the installed copies are used when they exist, and PATH otherwise. A
path typed in Settings → Playout → tools always wins. Resolving at use time
(not by writing paths into the settings) means an install upgraded from
v0.1.0, whose saved settings say `ffprobe`, picks up the bundled copies too.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from baja_studio.core.system_services import InstallLayout
from baja_studio.playout.config import ToolsSettings

BUNDLED_DIRECTORY_NAME = "ffmpeg"
FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"


class ToolSource(StrEnum):
    """Where a tool was found; the readiness check names it."""

    BUNDLED = "bundled"
    SETTINGS = "settings"
    PATH = "PATH"


@dataclass(frozen=True)
class ResolvedTool:
    """One tool: what to run, where it came from, and whether it is there at all."""

    name: str
    command: str
    source: ToolSource
    found_at: str | None

    def describe(self) -> str:
        """For the readiness line, e.g. `ffprobe: bundled (C:\\...\\ffprobe.exe)`."""
        return f"{self.name}: {self.source.value} ({self.found_at or 'not found'})"


def bundled_directory(layout: InstallLayout) -> Path:
    """`<program folder>\\ffmpeg`, where the installer puts ffprobe.exe and ffmpeg.exe."""
    return layout.program_directory / BUNDLED_DIRECTORY_NAME


def resolve_tool(
    name: str,
    configured: str,
    layout: InstallLayout | None,
    which: Callable[[str], str | None] = shutil.which,
) -> ResolvedTool:
    """Resolve `name` (ffprobe or ffmpeg) as configured; see the module docstring for the order."""
    if configured == name and layout is not None:
        bundled = bundled_directory(layout) / f"{name}.exe"
        if bundled.is_file():
            return ResolvedTool(name, str(bundled), ToolSource.BUNDLED, str(bundled))
    source = ToolSource.PATH if configured == name else ToolSource.SETTINGS
    return ResolvedTool(name, configured, source, which(configured))


def resolve_tools(
    tools: ToolsSettings,
    layout: InstallLayout | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[ResolvedTool, ResolvedTool]:
    """ffprobe and ffmpeg for these settings; `layout` defaults to the installer's."""
    layout = layout if layout is not None else InstallLayout.from_environment()
    return (
        resolve_tool(FFPROBE, tools.ffprobe_path, layout, which),
        resolve_tool(FFMPEG, tools.ffmpeg_path, layout, which),
    )
