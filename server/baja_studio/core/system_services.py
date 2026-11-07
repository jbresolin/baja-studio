"""The Windows services the installer registers, and the folder it installs into.

The installer (installer/BajaStudio.iss) registers two services with NSSM:
`BajaStudio` (this service) and `BajaStudioGateway` (Caddy). It tells this
service where the program folder is through `BAJA_STUDIO_INSTALL_DIR`, so
the gateway module can find `nssm.exe` and `caddy.exe`.

A service never edits its own registration by hand: the only thing the
studio changes is the gateway's `AppEnvironmentExtra` (the Cloudflare token,
see modules/gateway), always through `nssm set`. Run from a checkout (no
install folder), there is no service to control and `NotInstalledServiceControl`
says so.
"""

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_INSTALL_DIR"
STUDIO_SERVICE = "BajaStudio"
GATEWAY_SERVICE = "BajaStudioGateway"
# `nssm restart` waits for the service to stop and start again; Caddy does both in seconds.
NSSM_TIMEOUT_SECONDS = 30.0
NOT_INSTALLED = "not installed"


@dataclass(frozen=True)
class InstallLayout:
    """Where the installer put things: `<program>\\nssm\\nssm.exe` and `<program>\\caddy`."""

    program_directory: Path

    @property
    def nssm_executable(self) -> Path:
        """NSSM, which registered and now controls both services."""
        return self.program_directory / "nssm" / "nssm.exe"

    @property
    def caddy_executable(self) -> Path:
        """The Caddy build with the Cloudflare DNS module."""
        return self.program_directory / "caddy" / "caddy.exe"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "InstallLayout | None":
        """The layout the installer announced, or None when running from a checkout."""
        value = (os.environ if environment is None else environment).get(
            INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE
        )
        return cls(Path(value)) if value else None


class ServiceControlError(Exception):
    """A service could not be read or changed; the message says what to do."""


class ServiceControl(Protocol):
    """Reads and changes the installed services (NSSM on the streaming PC)."""

    def status(self, service: str) -> str:
        """`SERVICE_RUNNING`, `SERVICE_STOPPED`... or "not installed"."""

    def environment(self, service: str) -> dict[str, str]:
        """The extra environment variables the service's program is started with."""

    def set_environment(self, service: str, variables: Mapping[str, str]) -> None:
        """Replace those variables; they take effect when the service next starts."""

    def restart(self, service: str) -> None:
        """Stop and start the service, waiting for both."""


class NotInstalledServiceControl:
    """Used when running from a checkout: there are no services to control."""

    def status(self, service: str) -> str:
        """Always "not installed"."""
        return NOT_INSTALLED

    def environment(self, service: str) -> dict[str, str]:
        """No service, so no environment."""
        return {}

    def set_environment(self, service: str, variables: Mapping[str, str]) -> None:
        """Refused: nothing could pass the variables on."""
        raise ServiceControlError(
            f"{service} is not installed as a service here; install with the Baja Studio installer"
        )

    def restart(self, service: str) -> None:
        """Refused, as for `set_environment`."""
        raise ServiceControlError(f"{service} is not installed as a service here")


CommandRunner = Callable[[list[str], float], "subprocess.CompletedProcess[bytes]"]


def run_hidden(
    arguments: list[str], timeout_seconds: float
) -> "subprocess.CompletedProcess[bytes]":
    """Run a program without a console window and capture what it prints."""
    # Only Windows has the flag (and consoles to hide).
    flags: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        arguments, capture_output=True, timeout=timeout_seconds, check=False, creationflags=flags
    )


def decode_nssm_output(raw: bytes) -> str:
    """NSSM prints UTF-16 when its output is captured; plain text otherwise.

    A captured `nssm get` therefore arrives with a zero byte after every
    ASCII letter, which would otherwise end up in the values.
    """
    if b"\x00" in raw:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.lstrip("﻿").replace("\x00", "").strip()


def parse_environment_lines(text: str) -> dict[str, str]:
    """`KEY=value` lines (as `nssm get ... AppEnvironmentExtra` prints them) into a dict."""
    variables: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key:
            variables[key] = value
    return variables


class NssmServiceControl:
    """`ServiceControl` through `nssm.exe`; every call waits at most 30 s."""

    def __init__(self, nssm_executable: Path, run: CommandRunner = run_hidden) -> None:
        """`run` is replaced in tests to see the command lines without running NSSM."""
        self._nssm = str(nssm_executable)
        self._run = run

    def status(self, service: str) -> str:
        """What `nssm status` says, or "not installed" when NSSM does not know the service."""
        result = self._call(["status", service], check=False)
        text = decode_nssm_output(result.stdout)
        if result.returncode != 0 or not text.startswith("SERVICE_"):
            return NOT_INSTALLED
        return text.splitlines()[0]

    def environment(self, service: str) -> dict[str, str]:
        """`nssm get <service> AppEnvironmentExtra`, parsed."""
        result = self._call(["get", service, "AppEnvironmentExtra"])
        return parse_environment_lines(decode_nssm_output(result.stdout))

    def set_environment(self, service: str, variables: Mapping[str, str]) -> None:
        """`nssm set ... AppEnvironmentExtra K=V ...`, or `nssm reset` when there are none.

        The values (a token) are on NSSM's command line for a moment, visible
        only to administrators; they are never written to a file or the log.
        """
        if variables:
            pairs = [f"{key}={value}" for key, value in sorted(variables.items())]
            self._call(["set", service, "AppEnvironmentExtra", *pairs])
        else:
            self._call(["reset", service, "AppEnvironmentExtra"])

    def restart(self, service: str) -> None:
        """`nssm restart <service>`."""
        self._call(["restart", service])

    def _call(
        self, arguments: list[str], check: bool = True
    ) -> "subprocess.CompletedProcess[bytes]":
        try:
            result = self._run([self._nssm, *arguments], NSSM_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceControlError(f"nssm {arguments[0]} failed: {error}") from error
        if check and result.returncode != 0:
            # Only the verb and service: the arguments of `set` may hold a secret.
            message = decode_nssm_output(result.stderr) or decode_nssm_output(result.stdout)
            raise ServiceControlError(
                f"nssm {arguments[0]} {arguments[1]} failed (exit {result.returncode}): {message}"
            )
        return result


def service_control_for(layout: InstallLayout | None) -> ServiceControl:
    """NSSM when installed, otherwise the stand-in that says there is nothing to control."""
    if layout is None or not layout.nssm_executable.is_file():
        return NotInstalledServiceControl()
    return NssmServiceControl(layout.nssm_executable)
