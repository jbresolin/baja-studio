"""Tests for the camera node's tray (`pythonw -m baja_studio_tray --node`, ADR 0023).

The camera node runs on a trackside laptop as a Windows service. Its tray
icon is the same program as Baja Studio's tray, started with `--node`:

- the icon is a camera, with the same green, amber or red dot;
- every 5 s it asks the node on this laptop how it is
  (`/api/node/tray-status`), which needs no token and answers only the
  laptop itself;
- its tooltip and menu show one line per camera: its name, its video mode
  (node or OBS) and whether it is live, or why not;
- the menu opens the node's page, restarts the node's service, opens the
  logs folder, and quits.

No icon is shown and no node runs: `FakeNode` answers like the node and
`FakeIcon` stands in for the notification area, so these run anywhere.
"""

import contextlib
import io
import threading
from pathlib import Path

from baja_studio_tray.__main__ import main
from baja_studio_tray.actions import NodeTrayActions
from baja_studio_tray.app import StatusPoller, Tray, badge_icons
from baja_studio_tray.icons import ALL_SIZES, load_frames
from baja_studio_tray.labels import EN, PT_BR, system_labels
from baja_studio_tray.menu import CAMERA_LINE_SLOTS, node_menu_entries
from baja_studio_tray.profile import NODE, STUDIO
from baja_studio_tray.restart import restart_node
from baja_studio_tray.service import (
    HEALTH_TIMEOUT_SECONDS,
    InstallPaths,
    port_from_file,
    read_install_paths,
)
from baja_studio_tray.status import TOOLTIP_LIMIT, Badge, TrayStatus, status_from_node
from tests.fake_icon import FakeIcon
from tests.fake_node import FakeNode, node_answer, node_camera
from tests.test_actions import FakeDesktop
from tests.test_restart import FakeNssm

QUIT_DEADLINE_SECONDS = 2.0
PROGRAM = Path("Program Files", "BajaCameraNode")
DATA = Path("ProgramData", "BajaCameraNode")

# --- What the icon shows ------------------------------------------------------------------


def test_every_camera_live_is_green() -> None:
    answer = node_answer(
        "ok",
        node_camera("cam1", "Left S600", "node"),
        node_camera("cam2", "Right S600", "obs"),
    )

    status = status_from_node(answer, EN)

    assert status.badge == Badge.GREEN
    assert status.tooltip(EN) == "Baja camera node 0.7.0: 2 cameras live"
    # One line per camera for the menu: its name, its video mode, its state.
    assert status.camera_lines == ("Left S600 (node): live", "Right S600 (OBS): live")


def test_a_camera_in_trouble_turns_amber_and_says_why() -> None:
    answer = node_answer(
        "degraded",
        node_camera("cam1", "Left S600", "node"),
        node_camera("cam2", "Right S600", "obs", status="degraded", reason="no OBS source"),
    )

    status = status_from_node(answer, EN)

    assert status.badge == Badge.AMBER
    # The tooltip names only the camera with the problem, with the node's reason.
    assert status.tooltip(EN) == "Baja camera node 0.7.0: Right S600: no OBS source"
    assert status.camera_lines[1] == "Right S600 (OBS): no OBS source"


def test_a_camera_with_no_reason_gets_a_word_for_its_state() -> None:
    answer = node_answer(
        "degraded",
        node_camera("cam1", "A", status="offline"),
        node_camera("cam2", "B", status="degraded"),
    )

    lines = status_from_node(answer, EN).camera_lines

    assert lines == ("A (node): offline", "B (node): trouble")


def test_a_camera_without_a_name_is_shown_by_its_id() -> None:
    status = status_from_node(node_answer("ok", node_camera("cam3", label="")), EN)

    assert status.camera_lines == ("cam3 (node): live",)


def test_a_node_with_no_cameras_yet_is_amber() -> None:
    # Freshly installed: nothing to stream yet, so not "all good".
    status = status_from_node({"status": "ok", "version": "0.7.0", "cameras": []}, EN)

    assert status.badge == Badge.AMBER
    assert status.summary == "no cameras set up"
    assert status.camera_lines == ()


def test_a_degraded_node_with_live_cameras_is_amber() -> None:
    # Something else on the node is wrong (its USB link, say) though the cameras are fine.
    status = status_from_node(node_answer("degraded", node_camera()), EN)

    assert status.badge == Badge.AMBER
    assert status.summary == "attention: Baja camera node"


def test_a_node_that_does_not_answer_is_red() -> None:
    status = status_from_node(None, EN)

    assert status.badge == Badge.RED
    assert status.tooltip(EN) == "Baja camera node: the node is not answering"


def test_an_unknown_status_is_red() -> None:
    status = status_from_node(node_answer("fail"), EN)

    assert status.badge == Badge.RED


def test_the_words_are_portuguese_on_a_portuguese_windows() -> None:
    status = status_from_node(node_answer("ok", node_camera(), node_camera("cam2", "B")), PT_BR)

    assert status.summary == "2 câmeras no ar"
    assert status.camera_lines[0] == "Left S600 (node): no ar"


def test_a_long_tooltip_is_cut_to_what_windows_shows() -> None:
    cameras = [
        node_camera(f"cam{n}", f"Camera {n}", status="degraded", reason="x" * 40) for n in range(4)
    ]

    tooltip = status_from_node(node_answer("degraded", *cameras), EN).tooltip(EN)

    assert len(tooltip) == TOOLTIP_LIMIT
    assert tooltip.endswith("…")


# --- Talking to the node ------------------------------------------------------------------


def test_the_tray_asks_the_node_on_this_laptop_at_its_port() -> None:
    node = FakeNode(port=8899)

    status = node.client().read_status(EN)

    assert status.badge == Badge.GREEN
    # One request, to this laptop (127.0.0.1), at the node's port, with no token.
    assert node.requests == [
        ("GET", "http://127.0.0.1:8899/api/node/tray-status", None, HEALTH_TIMEOUT_SECONDS)
    ]


def test_a_stopped_node_turns_the_icon_red_at_the_next_poll() -> None:
    node = FakeNode()
    shown: list[TrayStatus] = []
    poller = StatusPoller(node.client(), EN, shown.append)
    poller.poll_once()

    node.answering = False
    poller.poll_once()

    assert [status.badge for status in shown] == [Badge.GREEN, Badge.RED]


def test_the_port_comes_from_the_file_the_node_writes(tmp_path: Path) -> None:
    port = port_from_file(tmp_path)
    # No file yet (the node has never started): the default port.
    assert port() == 8891

    # The node started on a port changed on its page; the tray reads it at the next poll.
    (tmp_path / "port.txt").write_text("8899\n", encoding="ascii")
    assert port() == 8899

    # A file that makes no sense is ignored.
    (tmp_path / "port.txt").write_text("not a port", encoding="ascii")
    assert port() == 8891
    (tmp_path / "port.txt").write_text("70000", encoding="ascii")
    assert port() == 8891


def test_the_node_s_folders_come_from_its_own_registry_key() -> None:
    # The node installer writes HKLM\Software\BajaCameraNode, not the studio's key.
    registry = {
        "InstallDirectory": r"C:\Program Files\BajaCameraNode",
        "DataDirectory": r"D:\Node",
    }

    paths = read_install_paths(
        registry.get, python_executable="unused", environment={}, profile=NODE
    )

    assert paths.logs_directory == Path(r"D:\Node") / "logs"
    assert paths.nssm_executable == Path(r"C:\Program Files\BajaCameraNode") / "nssm" / "nssm.exe"


def test_without_the_registry_the_node_s_data_is_in_program_data() -> None:
    paths = read_install_paths(
        lambda name: None,
        python_executable="unused",
        environment={"ProgramData": "PD"},
        profile=NODE,
    )

    assert paths.data_directory == Path("PD") / "BajaCameraNode"


# --- The icon -----------------------------------------------------------------------------


def test_the_camera_icon_has_every_size_the_tray_needs() -> None:
    assert sorted(load_frames(NODE.icon_path)) == sorted(ALL_SIZES)


def test_the_node_s_badge_icons_are_drawn_on_the_camera(tmp_path: Path) -> None:
    paths = badge_icons(tmp_path, NODE)

    assert set(paths) == set(Badge)
    for badge, path in paths.items():
        assert path.name == f"camera-node-{badge.value}.ico"
        assert sorted(load_frames(path)) == sorted(ALL_SIZES)
    # It is not the studio's artwork.
    assert NODE.icon_path != STUDIO.icon_path
    camera = load_frames(NODE.icon_path)[32].tobytes()
    assert camera != load_frames(STUDIO.icon_path)[32].tobytes()


def test_the_two_trays_can_run_side_by_side() -> None:
    # A PC with both the studio and a node: each tray has its own single-instance mutex.
    assert NODE.single_instance_mutex != STUDIO.single_instance_mutex


# --- The menu -----------------------------------------------------------------------------


def node_actions(desktop: FakeDesktop, port: int = 8891) -> NodeTrayActions:
    return NodeTrayActions(
        paths=InstallPaths(PROGRAM, DATA),
        labels=EN,
        notify=desktop.notifications.append,
        port=lambda: port,
        open_url=desktop.open_url,
        open_path=desktop.opened_paths.append,
        elevate=desktop.elevate,
        python_executable=str(PROGRAM / "python" / "pythonw.exe"),
    )


def test_the_menu_shows_a_line_per_camera_between_open_and_restart() -> None:
    node = FakeNode(
        answer=node_answer("ok", node_camera("cam1", "Left S600"), node_camera("cam2", "Right"))
    )
    poller = StatusPoller(node.client(), EN, lambda status: None)
    poller.poll_once()

    entries = node_menu_entries(EN, node_actions(FakeDesktop()), poller, lambda: None)

    # What the user sees: hidden camera slots and separators left out.
    shown = [
        entry.text()
        for entry in entries
        if not entry.separator and (entry.visible is None or entry.visible())
    ]
    assert shown == [
        "Open the camera node's page",
        "Left S600 (node): live",
        "Right (node): live",
        "Restart the camera node",
        "Open logs folder",
        "Version 0.7.0",
        "Quit",
    ]
    # Double-clicking the icon opens the node's page.
    assert [entry.text() for entry in entries if entry.default] == ["Open the camera node's page"]
    # The camera lines are information only: greyed out, nothing to click.
    camera_entries = [entry for entry in entries if entry.text().endswith(": live")]
    assert all(entry.action is None for entry in camera_entries)


def test_the_camera_lines_follow_each_poll() -> None:
    # A tray menu has a fixed number of items, so the camera lines are slots
    # that show or hide as cameras come and go.
    node = FakeNode(answer=node_answer("ok", node_camera("cam1", "A")))
    poller = StatusPoller(node.client(), EN, lambda status: None)
    entries = node_menu_entries(EN, node_actions(FakeDesktop()), poller, lambda: None)

    def visible_camera_lines() -> list[str]:
        return [entry.text() for entry in entries if entry.visible is not None and entry.visible()]

    poller.poll_once()
    assert visible_camera_lines() == ["A (node): live"]

    node.answer = node_answer(
        "degraded", node_camera("cam1", "A"), node_camera("cam2", "B", status="offline")
    )
    poller.poll_once()
    assert visible_camera_lines() == ["A (node): live", "B (node): offline"]

    node.answering = False
    poller.poll_once()
    assert visible_camera_lines() == []
    assert sum(1 for entry in entries if entry.visible is not None) == CAMERA_LINE_SLOTS


def test_open_page_opens_the_node_on_this_laptop_at_its_port() -> None:
    desktop = FakeDesktop()

    node_actions(desktop, port=8899).open_page()

    assert desktop.opened_urls == ["http://localhost:8899/"]


def test_restart_asks_for_administrator_rights_and_restarts_the_node() -> None:
    desktop = FakeDesktop()

    node_actions(desktop).restart_node()

    # The tray runs as the user; restarting a service needs an administrator (UAC prompt).
    assert desktop.elevated == [
        (str(PROGRAM / "python" / "pythonw.exe"), "-m baja_studio_tray.restart --node")
    ]
    assert desktop.notifications == ["Restarting the camera node…"]


def test_a_cancelled_restart_says_so() -> None:
    desktop = FakeDesktop(uac_accepted=False)

    node_actions(desktop).restart_node()

    assert desktop.notifications == ["Restart cancelled."]


def test_open_logs_opens_the_node_s_logs_folder() -> None:
    desktop = FakeDesktop()

    node_actions(desktop).open_logs()

    assert desktop.opened_paths == [DATA / "logs"]


def test_the_elevated_restart_restarts_only_the_node_s_service() -> None:
    nssm = FakeNssm()
    nssm_path = PROGRAM / "nssm" / "nssm.exe"

    assert restart_node(nssm_path, nssm.run)

    assert nssm.commands == [[str(nssm_path), "restart", "BajaCameraNode"]]


def test_a_failed_restart_is_reported() -> None:
    nssm = FakeNssm(failing=["restart"])

    assert not restart_node(PROGRAM / "nssm" / "nssm.exe", nssm.run)


# --- The whole program --------------------------------------------------------------------


def test_main_with_node_runs_until_quit() -> None:
    # `pythonw -m baja_studio_tray --node`, with a fake node and a fake icon.
    icon = FakeIcon()
    trays: list[Tray] = []
    program = threading.Thread(
        target=main,
        args=(["--node"],),
        kwargs={"client": FakeNode().client(), "backend": icon, "on_started": trays.append},
        name="test-node-main",
    )
    program.start()
    assert icon.first_status.wait(QUIT_DEADLINE_SECONDS)
    assert icon.shown[0].product == "Baja camera node"

    icon.click(system_labels().quit)

    program.join(QUIT_DEADLINE_SECONDS)
    assert not program.is_alive()
    poll_thread = trays[0].poll_thread
    assert poll_thread is not None and not poll_thread.is_alive()


def test_the_smoke_test_flag_reports_the_node_and_its_cameras() -> None:
    # What the node installer's smoke test runs and reads.
    output = io.StringIO()
    node = FakeNode(answer=node_answer("ok", node_camera("cam1", "Left S600")))

    with contextlib.redirect_stdout(output):
        main(["--node", "--exit-after-first-poll"], client=node.client(), backend=FakeIcon())

    report = output.getvalue()
    assert "badge=green version=0.7.0" in report
    assert "tooltip=Baja camera node 0.7.0:" in report
    assert "camera=Left S600 (node):" in report
