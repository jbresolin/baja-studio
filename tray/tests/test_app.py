"""Tests for the poll loop and the menu (app.py, menu.py), without showing an icon."""

import threading
from pathlib import Path

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.app import StatusPoller, badge_icons
from baja_studio_tray.icons import ALL_SIZES, load_frames
from baja_studio_tray.labels import EN
from baja_studio_tray.menu import menu_entries
from baja_studio_tray.service import InstallPaths
from baja_studio_tray.status import Badge, TrayStatus
from tests.fake_studio import FakeStudio, health_answer


def test_the_icon_changes_only_when_the_status_does() -> None:
    studio = FakeStudio()
    shown: list[TrayStatus] = []
    poller = StatusPoller(studio.client(), EN, shown.append)

    # Two polls with the same answer: the icon is drawn once.
    poller.poll_once()
    poller.poll_once()
    # The service stops: red, drawn again.
    studio.answering = False
    poller.poll_once()

    assert [status.badge for status in shown] == [Badge.GREEN, Badge.RED]


def test_a_killed_service_turns_red_at_the_next_poll() -> None:
    # The brief asks for red within 5 s: one poll interval.
    studio = FakeStudio(health=health_answer("ok"))
    poller = StatusPoller(studio.client(), EN, lambda status: None)
    poller.poll_once()

    studio.answering = False

    assert poller.poll_once().badge == Badge.RED


def test_the_smoke_test_flag_polls_once_and_returns() -> None:
    studio = FakeStudio()
    poller = StatusPoller(studio.client(), EN, lambda status: None)

    poller.run(threading.Event(), exit_after_first_poll=True)

    assert len(studio.requests) == 1
    assert poller.version() == "0.2.0"


def test_one_icon_file_per_badge_is_written(tmp_path: Path) -> None:
    paths = badge_icons(tmp_path)

    assert set(paths) == set(Badge)
    for path in paths.values():
        assert sorted(load_frames(path)) == sorted(ALL_SIZES)


def test_the_menu_lists_the_briefs_items_in_order() -> None:
    studio = FakeStudio()
    actions = TrayActions(studio.client(), InstallPaths(Path("p"), Path("d")), EN, print)
    quit_calls: list[bool] = []

    entries = menu_entries(EN, actions, lambda: "0.2.0", lambda: quit_calls.append(True))

    texts = [entry.text() for entry in entries if not entry.separator]
    assert texts == [
        "Open Baja Studio",
        "Readiness",
        "Restart services",
        "Open logs folder",
        "Diagnostics bundle",
        "Version 0.2.0",
        "Quit",
    ]
    # Double-clicking the icon opens the studio; the version line does nothing.
    assert [entry.text() for entry in entries if entry.default] == ["Open Baja Studio"]
    version_line = next(entry for entry in entries if entry.text().startswith("Version"))
    assert version_line.action is None
    quit_entry = entries[-1]
    assert quit_entry.action is not None
    quit_entry.action()
    assert quit_calls == [True]
