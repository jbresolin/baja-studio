"""Tests for what each menu action does (actions.py), with every effect recorded by fakes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.labels import EN
from baja_studio_tray.service import InstallPaths
from tests.fake_studio import BUNDLE_NAME, FakeStudio

PROGRAM = Path("Program Files", "BajaStudio")
DATA = Path("ProgramData", "BajaStudio")
PYTHONW = str(PROGRAM / "python" / "pythonw.exe")


@dataclass
class FakeDesktop:
    """Records what the actions asked Windows to do."""

    opened_urls: list[str] = field(default_factory=list)
    opened_paths: list[Path] = field(default_factory=list)
    elevated: list[tuple[str, str]] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)
    uac_accepted: bool = True

    def open_url(self, url: str) -> bool:
        self.opened_urls.append(url)
        return True

    def elevate(self, executable: str, parameters: str) -> bool:
        self.elevated.append((executable, parameters))
        return self.uac_accepted


def make_actions(
    desktop: FakeDesktop, studio: FakeStudio, downloads: Path | None = None
) -> TrayActions:
    def run_now(work: Callable[[], None]) -> None:
        # Background work runs at once, so the test sees its outcome.
        work()

    return TrayActions(
        client=studio.client(),
        paths=InstallPaths(PROGRAM, DATA),
        labels=EN,
        notify=desktop.notifications.append,
        open_url=desktop.open_url,
        open_path=desktop.opened_paths.append,
        elevate=desktop.elevate,
        background=run_now,
        download_to=lambda: downloads or Path("Downloads"),
        python_executable=PYTHONW,
    )


def test_open_studio_and_readiness_open_their_pages() -> None:
    desktop = FakeDesktop()
    actions = make_actions(desktop, FakeStudio())

    actions.open_studio()
    actions.open_readiness()

    assert desktop.opened_urls == ["http://localhost:8888/", "http://localhost:8888/readiness"]


def test_restart_runs_the_restart_helper_as_administrator() -> None:
    desktop = FakeDesktop()

    make_actions(desktop, FakeStudio()).restart_services()

    # The same Python, elevated through UAC, running the restart module.
    assert desktop.elevated == [(PYTHONW, "-m baja_studio_tray.restart")]
    assert desktop.notifications == [EN.restart_started]


def test_a_cancelled_uac_prompt_is_reported() -> None:
    desktop = FakeDesktop(uac_accepted=False)

    make_actions(desktop, FakeStudio()).restart_services()

    assert desktop.notifications == [EN.restart_cancelled]


def test_open_logs_opens_the_data_folders_logs() -> None:
    desktop = FakeDesktop()

    make_actions(desktop, FakeStudio()).open_logs()

    assert desktop.opened_paths == [DATA / "logs"]


def test_the_diagnostics_bundle_is_saved_to_downloads_and_opened(tmp_path: Path) -> None:
    desktop = FakeDesktop()

    make_actions(desktop, FakeStudio(), downloads=tmp_path).diagnostics_bundle()

    saved = tmp_path / BUNDLE_NAME
    assert saved.is_file()
    assert desktop.opened_paths == [saved]
    assert desktop.notifications == [EN.bundle_building, EN.bundle_ready.format(path=saved)]


def test_a_stopped_service_gives_no_bundle_and_says_so(tmp_path: Path) -> None:
    desktop = FakeDesktop()

    make_actions(desktop, FakeStudio(answering=False), downloads=tmp_path).diagnostics_bundle()

    assert desktop.opened_paths == []
    assert desktop.notifications[-1].startswith("The bundle could not be built")
