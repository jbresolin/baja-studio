"""The tray's view of the service: its HTTP API, and where the installer put things.

Only the standard library: `urllib` with explicit timeouts (2 s for health,
60 s for building a diagnostics bundle), and the registry values the
installer writes under `HKLM\\Software\\BajaStudio`.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STUDIO_URL = "http://127.0.0.1:8888"
HEALTH_TIMEOUT_SECONDS = 2.0
BUNDLE_TIMEOUT_SECONDS = 60.0
REGISTRY_KEY = r"Software\BajaStudio"
STUDIO_SERVICE = "BajaStudio"
GATEWAY_SERVICE = "BajaStudioGateway"

# (method, url, JSON body or None, timeout) -> response body; raises OSError when it fails.
Fetch = Callable[[str, str, dict[str, Any] | None, float], bytes]


class StudioUnreachableError(Exception):
    """The service did not answer, or answered with an error; the message says which."""


def urllib_fetch(method: str, url: str, body: dict[str, Any] | None, timeout: float) -> bytes:
    """The real HTTP call."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content: bytes = response.read()
        return content


@dataclass(frozen=True)
class DiagnosticsBundle:
    """A bundle the service built: its file name and where to download it."""

    file_name: str
    url: str


class StudioClient:
    """The few calls the tray makes to the service on this PC."""

    def __init__(self, base_url: str = STUDIO_URL, fetch: Fetch = urllib_fetch) -> None:
        """`fetch` is replaced by a fake in tests."""
        self.base_url = base_url
        self._fetch = fetch

    def health(self) -> dict[str, Any] | None:
        """`/api/health`, or None when the service does not answer (red badge)."""
        try:
            answer = self._fetch("GET", f"{self.base_url}/api/health", None, HEALTH_TIMEOUT_SECONDS)
            health = json.loads(answer)
        except (OSError, ValueError):
            return None
        return health if isinstance(health, dict) else None

    def build_diagnostics_bundle(self) -> DiagnosticsBundle:
        """Ask the home module for a bundle, as the button on the home page does."""
        body = {"module": "home", "command": "diagnostics_bundle", "caller": "tray"}
        try:
            answer = self._fetch(
                "POST", f"{self.base_url}/api/commands", body, BUNDLE_TIMEOUT_SECONDS
            )
            result = json.loads(answer)["result"]
            return DiagnosticsBundle(str(result["file_name"]), str(result["url"]))
        except urllib.error.HTTPError as error:
            raise StudioUnreachableError(f"HTTP {error.code}") from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise StudioUnreachableError(str(error) or type(error).__name__) from error

    def download(self, bundle: DiagnosticsBundle, folder: Path) -> Path:
        """Save the bundle into `folder` (the user's Downloads); returns the file."""
        try:
            content = self._fetch(
                "GET", f"{self.base_url}{bundle.url}", None, BUNDLE_TIMEOUT_SECONDS
            )
        except OSError as error:
            raise StudioUnreachableError(str(error) or type(error).__name__) from error
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(bundle.file_name).name
        target.write_bytes(content)
        return target


@dataclass(frozen=True)
class InstallPaths:
    """The program and data folders, as the installer recorded them."""

    program_directory: Path
    data_directory: Path

    @property
    def nssm_executable(self) -> Path:
        """NSSM, which controls both services."""
        return self.program_directory / "nssm" / "nssm.exe"

    @property
    def logs_directory(self) -> Path:
        """`<data>\\logs`."""
        return self.data_directory / "logs"


def read_install_paths(
    read_registry: Callable[[str], str | None] | None = None,
    python_executable: str = sys.executable,
    environment: dict[str, str] | None = None,
) -> InstallPaths:
    """From the registry; without it, the folders next to this Python and the default data."""
    read = read_registry or _read_machine_value
    environment = dict(os.environ) if environment is None else environment
    program = read("InstallDirectory")
    data = read("DataDirectory")
    # This Python is <program>\python\pythonw.exe in an installed studio.
    program_directory = Path(program) if program else Path(python_executable).parent.parent
    default_data = Path(environment.get("ProgramData", r"C:\ProgramData")) / "BajaStudio"
    return InstallPaths(program_directory, Path(data) if data else default_data)


def _read_machine_value(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None
