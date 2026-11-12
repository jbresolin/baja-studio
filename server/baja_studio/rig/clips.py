"""The rig's demo clip library: short test-pattern videos made with ffmpeg.

    clips/delivery/<prova>/<car>/<passagem>.mp4   delivered clips (the receiver root),
                                                  announced by the fake orchestrator
    clips/delivery/enduro/42/extra-take.mp4       a file no manifest entry matches
    clips/patrocinadores/*.mp4                    a labelled local folder

ffmpeg is found by playout's own resolver (bundled copy, else PATH), so the
rig uses what the studio would. Generating is idempotent: a clip already
there is skipped, and each is written under a temporary name first, so an
interrupted run never leaves a half clip that looks finished.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from baja_studio.playout.config import ToolsSettings
from baja_studio.playout.tools import ResolvedTools, resolve_tools

DELIVERY_FOLDER = "delivery"
SPONSORS_FOLDER = "patrocinadores"
SPONSORS_LABEL = "Patrocinadores"
PARTIAL_SUFFIX = ".part"
FFMPEG_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class DemoClip:
    """One clip to make: where (relative to the clips folder), how long, which shape."""

    relative_path: str
    duration_s: float
    vertical: bool = False
    tone_hz: int | None = None

    @property
    def size(self) -> str:
        """Small frames, so the library is made in seconds: 640x360 or 360x640."""
        return "360x640" if self.vertical else "640x360"


DEFAULT_CLIPS = (
    DemoClip("delivery/enduro/42/1.mp4", 12, tone_hz=440),
    DemoClip("delivery/enduro/42/2.mp4", 25),
    DemoClip("delivery/enduro/7/1.mp4", 8, tone_hz=550),
    DemoClip("delivery/enduro/13/3.mp4", 40, tone_hz=330),
    DemoClip("delivery/suspensao/7/1.mp4", 15, vertical=True),
    DemoClip("delivery/suspensao/21/2.mp4", 30, tone_hz=660),
    DemoClip("delivery/aceleracao/13/1.mp4", 5),
    DemoClip("delivery/aceleracao/42/1.mp4", 6, vertical=True, tone_hz=880),
    DemoClip("delivery/enduro/42/extra-take.mp4", 10),
    DemoClip("patrocinadores/vinheta.mp4", 7, tone_hz=520),
    DemoClip("patrocinadores/patrocinador-vertical.mp4", 20, vertical=True, tone_hz=392),
    DemoClip("patrocinadores/intervalo.mp4", 35),
)

Report = Callable[[str], None]


def find_tools() -> ResolvedTools:
    """ffprobe and ffmpeg as playout would find them with blank settings."""
    return resolve_tools(ToolsSettings())


def ffmpeg_command(ffmpeg: str, clip: DemoClip, output: Path) -> list[str]:
    """The ffmpeg command line making `clip` at `output` (an MP4 whatever the file is called)."""
    duration = f"{clip.duration_s:g}"
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    command += ["-f", "lavfi", "-i", f"testsrc2=size={clip.size}:rate=30:duration={duration}"]
    if clip.tone_hz is not None:
        command += ["-f", "lavfi", "-i", f"sine=frequency={clip.tone_hz}:duration={duration}"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if clip.tone_hz is not None:
        command += ["-c:a", "aac", "-shortest"]
    return [*command, "-f", "mp4", str(output)]


def make_clip_library(
    folder: Path,
    ffmpeg: str | None,
    report: Report,
    clips: tuple[DemoClip, ...] = DEFAULT_CLIPS,
) -> list[Path]:
    """Make every clip missing under `folder`; returns the clips made now.

    With no ffmpeg nothing is made and `report` says how to get one: the
    rig still runs, with an empty library.
    """
    for folder_name in (DELIVERY_FOLDER, SPONSORS_FOLDER):
        (folder / folder_name).mkdir(parents=True, exist_ok=True)
    for leftover in folder.rglob(f"*{PARTIAL_SUFFIX}"):
        leftover.unlink()
    missing = [clip for clip in clips if not (folder / clip.relative_path).is_file()]
    if not missing:
        return []
    if ffmpeg is None:
        report(
            f"ffmpeg not found: {len(missing)} demo clips not made. Install ffmpeg (on PATH) "
            "or run from an installed studio's folder, then start the rig again."
        )
        return []
    report(f"making {len(missing)} demo clips in {folder} (once) ...")
    made = []
    for clip in missing:
        target = folder / clip.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        subprocess.run(
            ffmpeg_command(ffmpeg, clip, partial),
            check=True,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
        partial.replace(target)
        made.append(target)
    return made
