"""The tray process: the icon, its menu, and the 5-second poll of `/api/health`.

The only file that imports pystray, and only inside `run_tray`. pystray loads
its icon at Windows' large-icon size (32 px), so the 16-24 px silhouette
frames would never be used; `_multi_size_icon_class` loads the badge's ICO at
the small-icon size instead, letting Windows pick the frame made for it.
"""

import ctypes
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.icons import badged_frames, load_frames, save_ico
from baja_studio_tray.labels import Labels
from baja_studio_tray.menu import MenuEntry, menu_entries
from baja_studio_tray.service import StudioClient
from baja_studio_tray.status import Badge, TrayStatus, status_from_health

POLL_SECONDS = 5.0
NOTIFICATION_TITLE = "Baja Studio"
SM_CXSMICON = 49
SINGLE_INSTANCE_MUTEX = "Local\\BajaStudioTray"
ERROR_ALREADY_EXISTS = 183


class StatusPoller:
    """Polls the service and reports a new status when the badge or tooltip changes."""

    def __init__(
        self, client: StudioClient, labels: Labels, show: Callable[[TrayStatus], None]
    ) -> None:
        """`show` is called on the poll thread, with every status that differs from the last."""
        self._client = client
        self._labels = labels
        self._show = show
        self.status: TrayStatus | None = None

    def poll_once(self) -> TrayStatus:
        """Ask `/api/health` once; tell `show` if anything visible changed."""
        status = status_from_health(self._client.health(), self._labels)
        if status != self.status:
            self.status = status
            self._show(status)
        return status

    def run(self, stop: threading.Event, exit_after_first_poll: bool = False) -> None:
        """Poll every 5 s until `stop` is set (or once, for the smoke test)."""
        while not stop.is_set():
            self.poll_once()
            if exit_after_first_poll:
                return
            stop.wait(POLL_SECONDS)

    def version(self) -> str | None:
        """The service's version from the last answer."""
        return self.status.version if self.status else None


def badge_icons(directory: Path) -> dict[Badge, Path]:
    """One multi-size ICO per badge, written once at startup."""
    frames = load_frames()
    paths = {}
    for badge in Badge:
        paths[badge] = directory / f"baja-studio-{badge.value}.ico"
        save_ico(badged_frames(frames, badge), paths[badge])
    return paths


def already_running() -> bool:
    """True when another tray runs in this logon session (Windows named mutex)."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    # Kept open for the life of the process: the mutex is the "I am running" flag.
    kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    return bool(kernel32.GetLastError() == ERROR_ALREADY_EXISTS)


def run_tray(
    client: StudioClient,
    labels: Labels,
    make_actions: Callable[[Callable[[str], None]], TrayActions],
    exit_after_first_poll: bool = False,
) -> TrayStatus | None:
    """Show the icon until Quit; returns the last status (the smoke test prints it)."""
    import pystray
    from PIL import Image

    stop = threading.Event()
    icon_directory = Path(tempfile.mkdtemp(prefix="baja-studio-tray-"))
    try:
        icons = badge_icons(icon_directory)
        icon_class = _multi_size_icon_class(pystray)
        icon = icon_class("BajaStudio", title="Baja Studio")

        def show(status: TrayStatus) -> None:
            icon.ico_path = icons[status.badge]
            with Image.open(icons[status.badge]) as image:
                icon.icon = image.copy()
            icon.title = status.tooltip(labels)
            icon.update_menu()

        poller = StatusPoller(client, labels, show)
        actions = make_actions(lambda message: icon.notify(message, NOTIFICATION_TITLE))

        def quit_tray() -> None:
            stop.set()
            icon.stop()

        entries = menu_entries(labels, actions, poller.version, quit_tray)
        icon.menu = pystray.Menu(*(_menu_item(pystray, entry) for entry in entries))

        def setup(running_icon: Any) -> None:
            poller.poll_once()
            running_icon.visible = True
            if exit_after_first_poll:
                running_icon.stop()
                return
            stop.wait(POLL_SECONDS)
            poller.run(stop)

        icon.run(setup=setup)
        stop.set()
        return poller.status
    finally:
        shutil.rmtree(icon_directory, ignore_errors=True)


def _menu_item(pystray: Any, entry: MenuEntry) -> Any:
    if entry.separator:
        return pystray.Menu.SEPARATOR
    action = entry.action

    def on_click(icon: Any, item: Any) -> None:
        if action is not None:
            action()

    return pystray.MenuItem(
        lambda item: entry.text(),
        on_click,
        default=entry.default,
        enabled=action is not None,
    )


def _multi_size_icon_class(pystray: Any) -> Any:
    """pystray's Icon, loading the ICO at the tray's size on Windows (see module docstring)."""
    if sys.platform != "win32":
        return pystray.Icon
    from pystray._util import win32

    class MultiSizeIcon(pystray.Icon):  # type: ignore[misc,unused-ignore]
        ico_path: Path | None = None

        def _assert_icon_handle(self) -> None:
            if self._icon_handle:
                return
            if self.ico_path is None:
                super()._assert_icon_handle()
                return
            size = ctypes.windll.user32.GetSystemMetrics(SM_CXSMICON)
            self._icon_handle = win32.LoadImage(
                None, str(self.ico_path), win32.IMAGE_ICON, size, size, win32.LR_LOADFROMFILE
            )

    return MultiSizeIcon
