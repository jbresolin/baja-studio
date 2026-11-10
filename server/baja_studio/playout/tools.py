"""Which ffprobe and ffmpeg playout runs: the installer's copies, a configured path, or PATH.

The installer puts both in `<program folder>\\ffmpeg\\` (ADR 0010). A tools
setting left blank, or holding just the bare name (`ffprobe`, the default
before WP5c), is *automatic*: the installed copy when it exists, else PATH.
A path typed in Playout → Settings → Tools always wins. Resolving at use
time (not by writing paths into the settings) means an install upgraded from
v0.1.0, whose saved settings say `ffprobe`, picks up the bundled copies too.

This is the one resolver (WP5c §1): the prober, the page's banner and the
readiness check all ask it, so they can never disagree again (the v0.2.0
bench saw a banner saying "not found" while the scanner used the bundled
copies, because the banner did its own PATH lookup).
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

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


class ResolvedTools(NamedTuple):
    """Both tools, as every consumer sees them."""

    ffprobe: ResolvedTool
    ffmpeg: ResolvedTool


# What the module is given (tests pass one with a fake PATH lookup).
ToolResolver = Callable[[ToolsSettings], ResolvedTools]


def bundled_directory(layout: InstallLayout) -> Path:
    """`<program folder>\\ffmpeg`, where the installer puts ffprobe.exe and ffmpeg.exe."""
    return layout.program_directory / BUNDLED_DIRECTORY_NAME


def is_automatic(name: str, configured: str) -> bool:
    """True for a blank setting or the bare name: "use the bundled copy, else PATH"."""
    return configured.strip() in ("", name)


def resolve_tool(
    name: str,
    configured: str,
    layout: InstallLayout | None,
    which: Callable[[str], str | None] = shutil.which,
) -> ResolvedTool:
    """Resolve `name` (ffprobe or ffmpeg) as configured; see the module docstring for the order."""
    if not is_automatic(name, configured):
        command = configured.strip()
        return ResolvedTool(name, command, ToolSource.SETTINGS, which(command))
    if layout is not None:
        bundled = bundled_directory(layout) / f"{name}.exe"
        if bundled.is_file():
            return ResolvedTool(name, str(bundled), ToolSource.BUNDLED, str(bundled))
    return ResolvedTool(name, name, ToolSource.PATH, which(name))


def resolve_tools(
    tools: ToolsSettings,
    layout: InstallLayout | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ResolvedTools:
    """ffprobe and ffmpeg for these settings; `layout` defaults to the installer's."""
    layout = layout if layout is not None else InstallLayout.from_environment()
    return ResolvedTools(
        resolve_tool(FFPROBE, tools.ffprobe_path, layout, which),
        resolve_tool(FFMPEG, tools.ffmpeg_path, layout, which),
    )


def tools_status(tools: ResolvedTools) -> dict[str, Any]:
    """What the page shows: where each tool was found (None if not) and the banner text.

    With ffprobe missing nothing gets a duration or a thumbnail; with only
    ffmpeg missing, durations work but thumbnails do not.
    """
    missing = [tool.name for tool in tools if tool.found_at is None]
    problem = None
    if tools.ffprobe.found_at is None:
        problem = (
            f"durations and thumbnails unavailable — {' and '.join(missing)} not found; "
            "run the installer again, or set the path in Playout → Settings"
        )
    elif tools.ffmpeg.found_at is None:
        problem = (
            "thumbnails unavailable — ffmpeg not found; "
            "run the installer again, or set its path in Playout → Settings"
        )
    return {
        "ffprobe": tools.ffprobe.found_at,
        "ffmpeg": tools.ffmpeg.found_at,
        "sources": {tool.name: tool.source.value for tool in tools},
        "problem": problem,
    }
