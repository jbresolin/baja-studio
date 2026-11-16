"""The tray's view of the service: its HTTP API, and where the installer put things.

Only the standard library: `urllib` with explicit timeouts (2 s for health
and the last readiness run, 60 s for building a diagnostics bundle), and the registry values the
installer writes under `HKLM\\Software\\BajaStudio` (the camera node's:
`HKLM\\Software\\BajaCameraNode`).

`StudioClient` talks to Baja Studio; `NodeClient` to the camera node, whose
tray reads one small answer meant for it (`/api/node/tray-status`, which
needs no token and answers this PC only; ADR 0023). Both offer
`read_status(labels)`, which is all the poll loop needs.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from baja_studio_tray.labels import Labels
from baja_studio_tray.profile import STUDIO, TrayProfile
from baja_studio_tray.status import TrayStatus, status_from_health, status_from_node

STUDIO_URL = "http://127.0.0.1:8888"
HEALTH_TIMEOUT_SECONDS = 2.0
BUNDLE_TIMEOUT_SECONDS = 60.0
REGISTRY_KEY = r"Software\BajaStudio"
STUDIO_SERVICE = "BajaStudio"
GATEWAY_SERVICE = "BajaStudioGateway"
NODE_SERVICE = "BajaCameraNode"
NODE_PORT = 8891
NODE_TRAY_STATUS_PATH = "/api/node/tray-status"
# The node writes the port it serves on here at each start (its port is a setting).
NODE_PORT_FILE = "port.txt"

# (method, url, JSON body or None, timeout) -> response body; raises OSError when it fails.
Fetch = Callable[[str, str, dict[str, Any] | None, float], bytes]


class StatusSource(Protocol):
    """What the poll loop asks every 5 s: the badge, tooltip (and camera lines) right now."""

    def read_status(self, labels: Labels) -> TrayStatus:
        """One poll's status; never raises (a service that does not answer is red)."""


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

    def read_status(self, labels: Labels) -> TrayStatus:
        """`/api/health`, then (if it answered) the last readiness run, as a badge and tooltip."""
        health = self.health()
        last_run = self.last_readiness_run() if health is not None else None
        return status_from_health(health, labels, last_run)

    def last_readiness_run(self) -> dict[str, Any] | None:
        """`/api/readiness/last`: the last finished run, or None (none yet, or no answer).

        Reading it never starts a run. A service older than the endpoint
        answers 404, which reads as "no run", so the badge follows health alone.
        """
        url = f"{self.base_url}/api/readiness/last"
        try:
            answer = json.loads(self._fetch("GET", url, None, HEALTH_TIMEOUT_SECONDS))
        except (OSError, ValueError):
            return None
        last_run = answer.get("last_run") if isinstance(answer, dict) else None
        return last_run if isinstance(last_run, dict) else None

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


class NodeClient:
    """The one call the camera node's tray makes: `/api/node/tray-status` on this laptop."""

    def __init__(
        self, port: Callable[[], int] = lambda: NODE_PORT, fetch: Fetch = urllib_fetch
    ) -> None:
        """`port()` is asked at every poll (`port_from_file`): the node's port is a setting,
        and the node may restart on a new one while the tray runs."""
        self._port = port
        self._fetch = fetch

    @property
    def base_url(self) -> str:
        """The node on this laptop, at its current port."""
        return f"http://127.0.0.1:{self._port()}"

    def tray_status(self) -> dict[str, Any] | None:
        """The node's answer, or None when it does not answer (red badge)."""
        url = f"{self.base_url}{NODE_TRAY_STATUS_PATH}"
        try:
            answer = json.loads(self._fetch("GET", url, None, HEALTH_TIMEOUT_SECONDS))
        except (OSError, ValueError):
            return None
        return answer if isinstance(answer, dict) else None

    def read_status(self, labels: Labels) -> TrayStatus:
        """The node's status and camera lines (`status.status_from_node`)."""
        return status_from_node(self.tray_status(), labels)


def port_from_file(data_directory: Path) -> Callable[[], int]:
    """Reads `<data>\\port.txt` each time it is called; 8891 when it is missing or unreadable."""

    def read() -> int:
        try:
            port = int((data_directory / NODE_PORT_FILE).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return NODE_PORT
        return port if 0 < port < 65536 else NODE_PORT

    return read


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
    profile: TrayProfile = STUDIO,
) -> InstallPaths:
    """From the registry; without it, the folders next to this Python and the default data.

    `profile` says whose: the studio's (`HKLM\\Software\\BajaStudio`) or the camera node's.
    """
    read = read_registry or (lambda name: _read_machine_value(profile.registry_key, name))
    environment = dict(os.environ) if environment is None else environment
    program = read("InstallDirectory")
    data = read("DataDirectory")
    # This Python is <program>\python\pythonw.exe in an installed studio.
    program_directory = Path(program) if program else Path(python_executable).parent.parent
    program_data = Path(environment.get("ProgramData", r"C:\ProgramData"))
    default_data = program_data / profile.default_data_folder
    return InstallPaths(program_directory, Path(data) if data else default_data)


def _read_machine_value(registry_key: str, name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, registry_key) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None
