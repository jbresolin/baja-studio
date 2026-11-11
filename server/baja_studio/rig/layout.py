"""Where the rig keeps its things, and the free local ports its fakes listen on.

Everything lives in one folder (`%LOCALAPPDATA%\\BajaStudioRig` on Windows,
`~/.local/share/baja-studio-rig` elsewhere), apart from the real studio's
data, so `--reset` can wipe it without touching an installed studio:

    clips/       the demo clip library (kept between runs; generating it takes a while)
    studio/      the rig studio's data folder (database, logs)
    rig-settings.json   the settings bundle, rewritten on every run (ports change)
"""

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

LOOPBACK = "127.0.0.1"
BUNDLE_FILE_NAME = "rig-settings.json"


def default_rig_directory() -> Path:
    """The rig's folder for this user."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "BajaStudioRig"
    return Path.home() / ".local" / "share" / "baja-studio-rig"


@dataclass(frozen=True)
class RigLayout:
    """The rig's folders under one root."""

    root: Path

    @property
    def clips(self) -> Path:
        """The demo clip library."""
        return self.root / "clips"

    @property
    def studio_data(self) -> Path:
        """The rig studio's data folder (`--data-dir`)."""
        return self.root / "studio"

    @property
    def bundle(self) -> Path:
        """The settings bundle the studio imports."""
        return self.root / BUNDLE_FILE_NAME

    def create(self) -> None:
        """Make the folders that must exist before anything starts."""
        self.clips.mkdir(parents=True, exist_ok=True)
        self.studio_data.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        """`--reset`: forget everything, clips and studio data included."""
        if self.root.exists():
            shutil.rmtree(self.root)


def free_tcp_port() -> int:
    """A TCP port nobody listens on right now, on the loopback address.

    The operating system picks it; there is a short window before the fake
    binds it, which is fine on a desk PC (a clash shows as a fake that does
    not start, and the rig says so).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def free_udp_port() -> int:
    """A UDP port nobody uses right now, on the loopback address (the XR18, VISCA)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])
