"""Tests for the poll loop, Quit and the menu (app.py, menu.py, __main__.py).

No icon is shown: `FakeIcon` stands in for pystray (a loop on the calling
thread, a setup thread, clicks run on the loop), so these run on any CI runner.
"""

import contextlib
import io
import threading
from pathlib import Path

from baja_studio_tray.__main__ import main
from baja_studio_tray.actions import TrayActions
from baja_studio_tray.app import StatusPoller, Tray, badge_icons, run_tray
from baja_studio_tray.icons import ALL_SIZES, load_frames
from baja_studio_tray.labels import EN, system_labels
from baja_studio_tray.menu import menu_entries
from baja_studio_tray.service import InstallPaths
from baja_studio_tray.status import Badge, TrayStatus
from tests.fake_icon import FakeIcon
from tests.fake_studio import FakeStudio, health_answer

# Generous: a working Quit takes milliseconds; the brief allows 2 s for the process.
QUIT_DEADLINE_SECONDS = 2.0


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


def test_the_readiness_counts_reach_the_badge_through_the_poll() -> None:
    # Health is ok, but the last readiness run had a warning: the poll asks
    # for both and the badge goes amber with the count.
    studio = FakeStudio(last_readiness_run={"started_at": "2026-11-10T14:32:00", "warnings": 1})
    poller = StatusPoller(studio.client(), EN, lambda status: None)

    status = poller.poll_once()

    assert status.badge == Badge.AMBER
    assert status.summary == "readiness at 14:32: 1 warning"
    assert [url for _, url, _, _ in studio.requests] == [
        "http://studio.test/api/health",
        "http://studio.test/api/readiness/last",
    ]


def test_a_service_that_does_not_answer_is_not_asked_about_readiness() -> None:
    studio = FakeStudio(answering=False)
    poller = StatusPoller(studio.client(), EN, lambda status: None)

    assert poller.poll_once().badge == Badge.RED
    assert len(studio.requests) == 1


def run_tray_in_background(icon: FakeIcon) -> tuple[threading.Thread, list[Tray]]:
    """Start `run_tray` on its own thread (it blocks like the real icon loop)."""
    trays: list[Tray] = []
    studio = FakeStudio()
    thread = threading.Thread(
        target=run_tray,
        args=(studio.client(), EN, lambda notify: TrayActions(studio.client(), PATHS, EN, notify)),
        kwargs={"backend": icon, "on_started": trays.append},
        name="test-icon-loop",
    )
    thread.start()
    return thread, trays


PATHS = InstallPaths(Path("p"), Path("d"))


def test_quit_stops_the_icon_loop_and_the_poll_thread() -> None:
    # The tray runs with a fake icon; its poll thread draws the first status.
    icon = FakeIcon()
    loop, trays = run_tray_in_background(icon)
    assert icon.first_status.wait(QUIT_DEADLINE_SECONDS)
    poll_thread = trays[0].poll_thread
    assert poll_thread is not None and poll_thread.is_alive()
    # The poll thread is a daemon: even if it were stuck, it could not keep
    # the process alive after the icon loop ends.
    assert poll_thread.daemon

    # The user clicks Quit; like pystray, the click runs on the icon loop.
    icon.click("Quit")

    # The loop ends (run_tray returns), the icon was told to stop, and the
    # poll thread has finished, not merely been asked to.
    loop.join(QUIT_DEADLINE_SECONDS)
    assert not loop.is_alive()
    assert icon.stop_calls >= 1
    assert not poll_thread.is_alive()


def test_quit_from_the_icon_loop_returns_without_waiting_for_a_poll_interval() -> None:
    # Quit is clicked right after the first status; the poll thread is then in
    # its 5 s wait, which Quit must cut short rather than sit through.
    icon = FakeIcon()
    loop, _ = run_tray_in_background(icon)
    assert icon.first_status.wait(QUIT_DEADLINE_SECONDS)

    icon.click("Quit")

    loop.join(QUIT_DEADLINE_SECONDS)
    assert not loop.is_alive()
    # Only the first poll happened: nothing was drawn after Quit.
    assert len(icon.shown) == 1


def test_quit_before_the_poll_thread_starts_still_ends_the_loop() -> None:
    # Quit called before the icon is ready: no poll thread is started later.
    icon = FakeIcon()
    tray = Tray(FakeStudio().client(), EN, icon)

    tray.quit()
    tray.start_polling()

    assert icon.stop_calls == 1
    assert tray.poll_thread is None


def test_main_returns_after_quit() -> None:
    # The whole program, as `pythonw -m baja_studio_tray` runs it, but with a
    # fake service and a fake icon. It must return once Quit is clicked.
    icon = FakeIcon()
    studio = FakeStudio()
    trays: list[Tray] = []
    program = threading.Thread(
        target=main,
        args=([],),
        kwargs={"client": studio.client(), "backend": icon, "on_started": trays.append},
        name="test-main",
    )
    program.start()
    assert icon.first_status.wait(QUIT_DEADLINE_SECONDS)

    # The menu speaks this PC's language; click its Quit ("Sair" in pt-BR).
    icon.click(system_labels().quit)

    program.join(QUIT_DEADLINE_SECONDS)
    assert not program.is_alive()
    poll_thread = trays[0].poll_thread
    assert poll_thread is not None and not poll_thread.is_alive()


def test_the_smoke_test_flag_polls_once_prints_and_returns() -> None:
    # The installer's smoke test: no click, one poll, then the program ends by itself.
    icon = FakeIcon()
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        main(["--exit-after-first-poll"], client=FakeStudio().client(), backend=icon)

    assert "badge=green version=0.2.0" in output.getvalue()
    assert icon.stop_calls >= 1
