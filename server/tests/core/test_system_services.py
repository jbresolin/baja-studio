"""Tests for the installed services and NSSM (core/system_services.py).

NSSM is not run: `NssmServiceControl` takes the function that runs programs,
and these tests pass one that records the command line and answers the way
NSSM does (including its habit of printing UTF-16 when captured).
"""

import subprocess
from pathlib import Path

import pytest

from baja_studio.core.system_services import (
    NOT_INSTALLED,
    InstallLayout,
    NotInstalledServiceControl,
    NssmServiceControl,
    ServiceControlError,
    decode_nssm_output,
    parse_environment_lines,
    service_control_for,
)

NSSM = Path("C:/Program Files/BajaStudio/nssm/nssm.exe")


class FakeNssm:
    """Answers command lines like NSSM would; remembers each one."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, tuple[int, bytes, bytes]] = {}

    def __call__(self, arguments: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(arguments)
        code, out, err = self.answers.get(arguments[1], (0, b"", b""))
        return subprocess.CompletedProcess(arguments, code, out, err)


def utf16(text: str) -> bytes:
    """What NSSM writes to a pipe: UTF-16 with a byte-order mark."""
    return "\ufeff".encode("utf-16-le") + text.encode("utf-16-le")


def test_nssm_utf16_output_is_decoded_without_stray_zero_bytes() -> None:
    assert decode_nssm_output(utf16("SERVICE_RUNNING\r\n")) == "SERVICE_RUNNING"
    assert decode_nssm_output(b"SERVICE_STOPPED\r\n") == "SERVICE_STOPPED"


def test_environment_lines_become_a_dictionary() -> None:
    text = "CLOUDFLARE_API_TOKEN=abc=def\r\nEMPTY=\r\nnot a pair\r\n"

    # Only the first "=" separates; a value may contain more.
    assert parse_environment_lines(text) == {"CLOUDFLARE_API_TOKEN": "abc=def", "EMPTY": ""}


def test_status_is_what_nssm_prints() -> None:
    nssm = FakeNssm()
    nssm.answers["status"] = (0, utf16("SERVICE_RUNNING\r\n"), b"")

    assert NssmServiceControl(NSSM, nssm).status("BajaStudio") == "SERVICE_RUNNING"
    assert nssm.calls == [[str(NSSM), "status", "BajaStudio"]]


def test_a_service_nssm_does_not_know_is_not_installed() -> None:
    nssm = FakeNssm()
    nssm.answers["status"] = (3, b"", utf16("Can't open service!"))

    assert NssmServiceControl(NSSM, nssm).status("BajaStudioGateway") == NOT_INSTALLED


def test_the_environment_is_read_and_set_with_nssm() -> None:
    nssm = FakeNssm()
    nssm.answers["get"] = (0, utf16("CLOUDFLARE_API_TOKEN=abc\r\n"), b"")
    control = NssmServiceControl(NSSM, nssm)

    assert control.environment("BajaStudioGateway") == {"CLOUDFLARE_API_TOKEN": "abc"}
    control.set_environment("BajaStudioGateway", {"CLOUDFLARE_API_TOKEN": "xyz"})
    control.set_environment("BajaStudioGateway", {})

    assert nssm.calls[1:] == [
        [str(NSSM), "set", "BajaStudioGateway", "AppEnvironmentExtra", "CLOUDFLARE_API_TOKEN=xyz"],
        # No variables left: reset the parameter rather than setting it empty.
        [str(NSSM), "reset", "BajaStudioGateway", "AppEnvironmentExtra"],
    ]


def test_a_failed_set_names_the_service_but_never_the_values() -> None:
    nssm = FakeNssm()
    nssm.answers["set"] = (5, b"", utf16("Access is denied."))
    control = NssmServiceControl(NSSM, nssm)

    with pytest.raises(ServiceControlError) as raised:
        control.set_environment("BajaStudioGateway", {"CLOUDFLARE_API_TOKEN": "secret-token"})

    assert str(raised.value) == "nssm set BajaStudioGateway failed (exit 5): Access is denied."
    assert "secret-token" not in str(raised.value)


def test_nssm_that_cannot_be_run_is_a_service_control_error() -> None:
    def missing(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("nssm.exe")

    with pytest.raises(ServiceControlError, match="nssm restart failed"):
        NssmServiceControl(NSSM, missing).restart("BajaStudioGateway")


def test_the_install_folder_comes_from_the_environment_the_installer_sets() -> None:
    layout = InstallLayout.from_environment({"BAJA_STUDIO_INSTALL_DIR": "C:/BajaStudio"})

    assert layout is not None
    assert layout.nssm_executable == Path("C:/BajaStudio/nssm/nssm.exe")
    assert layout.caddy_executable == Path("C:/BajaStudio/caddy/caddy.exe")
    assert InstallLayout.from_environment({}) is None


def test_without_an_installed_nssm_there_is_nothing_to_control(tmp_path: Path) -> None:
    assert isinstance(service_control_for(None), NotInstalledServiceControl)
    # The folder is announced but NSSM is not in it (a broken install).
    assert isinstance(service_control_for(InstallLayout(tmp_path)), NotInstalledServiceControl)

    (tmp_path / "nssm").mkdir()
    (tmp_path / "nssm" / "nssm.exe").write_bytes(b"")
    assert isinstance(service_control_for(InstallLayout(tmp_path)), NssmServiceControl)


def test_from_a_checkout_the_services_are_not_installed_and_cannot_be_changed() -> None:
    control = NotInstalledServiceControl()

    assert control.status("BajaStudio") == NOT_INSTALLED
    assert control.environment("BajaStudioGateway") == {}
    with pytest.raises(ServiceControlError, match="not installed"):
        control.set_environment("BajaStudioGateway", {"A": "1"})
    with pytest.raises(ServiceControlError, match="not installed"):
        control.restart("BajaStudioGateway")
