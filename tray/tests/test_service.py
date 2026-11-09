"""Tests for the tray's calls to the service and where it finds the folders (service.py)."""

from pathlib import Path

import pytest

from baja_studio_tray.service import (
    BUNDLE_TIMEOUT_SECONDS,
    HEALTH_TIMEOUT_SECONDS,
    StudioUnreachableError,
    read_install_paths,
)
from tests.fake_studio import BASE_URL, BUNDLE_NAME, BUNDLE_URL, FakeStudio


def test_health_is_read_with_a_short_timeout() -> None:
    studio = FakeStudio()

    assert studio.client().health() == studio.health

    # A poll every 5 s must never hang: 2 s at most per request.
    assert studio.requests == [("GET", f"{BASE_URL}/api/health", None, HEALTH_TIMEOUT_SECONDS)]


def test_a_stopped_service_gives_no_health() -> None:
    studio = FakeStudio(answering=False)

    assert studio.client().health() is None


def test_the_bundle_is_built_like_the_home_page_button_does(tmp_path: Path) -> None:
    studio = FakeStudio()
    client = studio.client()

    bundle = client.build_diagnostics_bundle()
    saved = client.download(bundle, tmp_path / "Downloads")

    # The same command as the page's button, marked as coming from the tray.
    method, url, body, timeout = studio.requests[0]
    assert (method, url) == ("POST", f"{BASE_URL}/api/commands")
    assert body == {"module": "home", "command": "diagnostics_bundle", "caller": "tray"}
    assert timeout == BUNDLE_TIMEOUT_SECONDS
    # Then the file is downloaded by its URL and saved under its own name.
    assert studio.requests[1][:2] == ("GET", f"{BASE_URL}{BUNDLE_URL}")
    assert saved == tmp_path / "Downloads" / BUNDLE_NAME
    assert saved.read_bytes() == studio.bundle_content


def test_a_refused_bundle_says_why() -> None:
    studio = FakeStudio(bundle_status=500)

    with pytest.raises(StudioUnreachableError, match="HTTP 500"):
        studio.client().build_diagnostics_bundle()


def test_the_folders_come_from_the_registry_when_installed() -> None:
    # What the installer writes under HKLM\Software\BajaStudio.
    registry = {"InstallDirectory": r"C:\Program Files\BajaStudio", "DataDirectory": r"D:\Studio"}

    paths = read_install_paths(registry.get, python_executable="unused", environment={})

    assert paths.program_directory == Path(r"C:\Program Files\BajaStudio")
    assert paths.logs_directory == Path(r"D:\Studio") / "logs"
    assert paths.nssm_executable == Path(r"C:\Program Files\BajaStudio") / "nssm" / "nssm.exe"


def test_without_the_registry_the_folders_are_guessed_from_this_python() -> None:
    # An installer older than v0.2.0 did not write InstallDirectory; this Python
    # is <program>\python\pythonw.exe, and the data folder is the default one.
    paths = read_install_paths(
        lambda name: None,
        python_executable=str(Path("Program", "python", "pythonw.exe")),
        environment={"ProgramData": "PD"},
    )

    assert paths.program_directory == Path("Program")
    assert paths.data_directory == Path("PD") / "BajaStudio"
