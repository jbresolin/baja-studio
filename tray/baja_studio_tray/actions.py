"""What each menu item does. Every effect on the PC is passed in, so tests use fakes.

- *Open Baja Studio*, *Readiness*: the page in the default browser;
- *Restart services*: `python -m baja_studio_tray.restart`, elevated through UAC;
- *Open logs folder*: `<data>\\logs` in Explorer;
- *Diagnostics bundle*: the service builds one, the tray saves it to the
  user's Downloads folder and opens it.
"""

import ctypes
import os
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baja_studio_tray.labels import Labels
from baja_studio_tray.service import InstallPaths, StudioClient, StudioUnreachableError

PAGE_URL = "http://localhost:8888"
READINESS_PATH = "/readiness"
RESTART_MODULE = "baja_studio_tray.restart"
# ShellExecute returns a value above 32 when it started the program.
SHELL_EXECUTE_OK_ABOVE = 32
SW_HIDE = 0


def open_in_explorer(path: Path) -> None:
    """Open a folder or file the way a double-click would."""
    os.startfile(path)  # type: ignore[attr-defined,unused-ignore]


def run_elevated(executable: str, parameters: str) -> bool:
    """Start a program as administrator (UAC prompt); False when refused or cancelled."""
    result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined,unused-ignore]
        None, "runas", executable, parameters, None, SW_HIDE
    )
    return int(result) > SHELL_EXECUTE_OK_ABOVE


def run_in_background(work: Callable[[], None]) -> None:
    """Menu callbacks must return at once; slow work goes to a thread."""
    threading.Thread(target=work, name="tray-action", daemon=True).start()


def downloads_folder() -> Path:
    """The user's Downloads folder."""
    return Path.home() / "Downloads"


@dataclass
class TrayActions:
    """The menu's actions, with the outside world injected."""

    client: StudioClient
    paths: InstallPaths
    labels: Labels
    notify: Callable[[str], None]
    open_url: Callable[[str], object] = webbrowser.open
    open_path: Callable[[Path], None] = open_in_explorer
    elevate: Callable[[str, str], bool] = run_elevated
    background: Callable[[Callable[[], None]], None] = run_in_background
    download_to: Callable[[], Path] = downloads_folder
    python_executable: str = field(default_factory=lambda: sys.executable)

    def open_studio(self) -> None:
        """The home page."""
        self.open_url(f"{PAGE_URL}/")

    def open_readiness(self) -> None:
        """The Readiness page, where the checks are run."""
        self.open_url(f"{PAGE_URL}{READINESS_PATH}")

    def restart_services(self) -> None:
        """Ask for administrator rights and restart both services (see restart.py)."""
        if self.elevate(self.python_executable, f"-m {RESTART_MODULE}"):
            self.notify(self.labels.restart_started)
        else:
            self.notify(self.labels.restart_cancelled)

    def open_logs(self) -> None:
        """The service's logs folder."""
        self.open_path(self.paths.logs_directory)

    def diagnostics_bundle(self) -> None:
        """Build, download and open a bundle, off the menu's thread."""
        self.notify(self.labels.bundle_building)
        self.background(self._build_and_open_bundle)

    def _build_and_open_bundle(self) -> None:
        try:
            bundle = self.client.build_diagnostics_bundle()
            saved = self.client.download(bundle, self.download_to())
        except (StudioUnreachableError, OSError) as error:
            self.notify(self.labels.bundle_failed.format(error=error))
            return
        self.notify(self.labels.bundle_ready.format(path=saved))
        self.open_path(saved)
