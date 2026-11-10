"""The tray process: the icon, its menu, and the 5-second poll of `/api/health`.

`Tray` holds the logic (poll thread, Quit) and talks to the icon through an
`IconBackend`, so tests drive it with a fake. `PystrayBackend` is the real
icon and the only code that imports pystray, and only inside `run`. pystray
loads its icon at Windows' large-icon size (32 px), so the 16-24 px
silhouette frames would never be used; `_multi_size_icon_class` loads the
badge's ICO at the small-icon size instead, letting Windows pick the frame
made for it.

Threads (ADR 0014): pystray's loop runs on the main thread and calls the
menu actions there; its setup thread only starts our poll thread and
returns, so `Icon.stop()` never waits for it. The poll thread is a daemon
of ours: Quit sets the stop event, stops the loop and joins the poll thread
for at most a second, and nothing left can keep the process alive.
"""

import ctypes
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.icons import badged_frames, load_frames, save_ico
from baja_studio_tray.labels import Labels
from baja_studio_tray.menu import MenuEntry, menu_entries
from baja_studio_tray.service import StudioClient
from baja_studio_tray.status import Badge, TrayStatus, status_from_health

POLL_SECONDS = 5.0
# Quit waits this long for the poll thread (at most one 2 s request is in flight;
# the thread is a daemon, so the process exits even if it is still waiting).
QUIT_JOIN_SECONDS = 1.0
NOTIFICATION_TITLE = "Baja Studio"
SM_CXSMICON = 49
SINGLE_INSTANCE_MUTEX = "Local\\BajaStudioTray"
ERROR_ALREADY_EXISTS = 183

# The single-instance mutex, held for the life of the process once taken.
_instance_mutex: int | None = None


class IconBackend(Protocol):
    """What the tray needs from an icon in the notification area."""

    def run(self, entries: list[MenuEntry], on_ready: Callable[[], None]) -> None:
        """Show the menu and run the icon loop until `stop`; `on_ready` once it runs."""

    def show(self, status: TrayStatus) -> None:
        """Draw the badge and the tooltip (from the poll thread)."""

    def notify(self, message: str) -> None:
        """A notification balloon."""

    def stop(self) -> None:
        """End the loop; `run` returns. Safe from any thread, more than once."""


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

    def run(self, stop: threading.Event) -> None:
        """Poll now and every 5 s until `stop` is set (the body of the poll thread)."""
        while not stop.is_set():
            self.poll_once()
            stop.wait(POLL_SECONDS)

    def version(self) -> str | None:
        """The service's version from the last answer."""
        return self.status.version if self.status else None


class Tray:
    """The poll thread and Quit, around an icon backend."""

    def __init__(self, client: StudioClient, labels: Labels, backend: IconBackend) -> None:
        """Nothing runs until the backend calls `start_polling`."""
        self._backend = backend
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self.poller = StatusPoller(client, labels, self._show)

    def start_polling(self) -> None:
        """The backend's `on_ready`: poll now and every 5 s on a daemon thread of ours."""
        if self._stop.is_set():
            return
        self._poll_thread = threading.Thread(
            target=self.poller.run, args=(self._stop,), name="tray-poll", daemon=True
        )
        self._poll_thread.start()

    def poll_once_and_quit(self) -> None:
        """The smoke test's `on_ready`: one poll, then Quit."""
        self.poller.poll_once()
        self.quit()

    def quit(self) -> None:
        """The Quit menu item: stop polling, end the icon loop, wait briefly for the poll thread."""
        self._stop.set()
        self._backend.stop()
        thread = self._poll_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(QUIT_JOIN_SECONDS)

    @property
    def poll_thread(self) -> threading.Thread | None:
        """The running poll thread, once started (tests check it ends on Quit)."""
        return self._poll_thread

    def _show(self, status: TrayStatus) -> None:
        # A poll that ends after Quit must not touch an icon that is going away.
        if not self._stop.is_set():
            self._backend.show(status)


def run_tray(
    client: StudioClient,
    labels: Labels,
    make_actions: Callable[[Callable[[str], None]], TrayActions],
    exit_after_first_poll: bool = False,
    backend: IconBackend | None = None,
    on_started: Callable[[Tray], None] | None = None,
) -> TrayStatus | None:
    """Show the icon until Quit; returns the last status (the smoke test prints it).

    `backend` defaults to the real icon; `on_started` lets a test see the tray.
    """
    backend = backend if backend is not None else PystrayBackend(labels)
    tray = Tray(client, labels, backend)
    if on_started is not None:
        on_started(tray)
    actions = make_actions(backend.notify)
    entries = menu_entries(labels, actions, tray.poller.version, tray.quit)
    on_ready = tray.poll_once_and_quit if exit_after_first_poll else tray.start_polling
    backend.run(entries, on_ready)
    # However the loop ended, the poll thread ends too.
    tray.quit()
    return tray.poller.status


def badge_icons(directory: Path) -> dict[Badge, Path]:
    """One multi-size ICO per badge, written once at startup."""
    frames = load_frames()
    paths = {}
    for badge in Badge:
        paths[badge] = directory / f"baja-studio-{badge.value}.ico"
        save_ico(badged_frames(frames, badge), paths[badge])
    return paths


def already_running() -> bool:
    """True when another tray runs in this logon session (Windows named mutex).

    The last error is read the way ctypes guarantees (`use_last_error`): a
    plain `GetLastError()` call after `CreateMutexW` can see a value Python
    itself set in between, and then two trays run side by side, so Quit on
    one leaves the other's identical icon behind.
    """
    global _instance_mutex
    if sys.platform != "win32" or _instance_mutex is not None:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return True
    # Kept open for the life of the process: the mutex is the "I am running" flag.
    _instance_mutex = handle
    return False


class PystrayBackend:
    """The real icon in the notification area (pystray)."""

    def __init__(self, labels: Labels) -> None:
        """Nothing is shown until `run`."""
        self._labels = labels
        self._icon: Any = None
        self._icons: dict[Badge, Path] = {}
        self._stop_requested = threading.Event()

    def run(self, entries: list[MenuEntry], on_ready: Callable[[], None]) -> None:
        """pystray's loop, on this (the main) thread, until `stop`."""
        import pystray

        icon_directory = Path(tempfile.mkdtemp(prefix="baja-studio-tray-"))
        try:
            self._icons = badge_icons(icon_directory)
            icon_class = _multi_size_icon_class(pystray)
            self._icon = icon_class("BajaStudio", title="Baja Studio")
            self._icon.menu = pystray.Menu(*(_menu_item(pystray, entry) for entry in entries))
            if self._stop_requested.is_set():
                return
            # pystray joins its setup thread when stopped, so the setup only
            # hands over to the tray (which starts its own thread) and returns.
            self._icon.run(setup=lambda icon: on_ready())
        finally:
            shutil.rmtree(icon_directory, ignore_errors=True)

    def show(self, status: TrayStatus) -> None:
        """New badge and tooltip; the icon appears with the first one."""
        from PIL import Image

        icon = self._icon
        icon.ico_path = self._icons[status.badge]
        with Image.open(self._icons[status.badge]) as image:
            icon.icon = image.copy()
        icon.title = status.tooltip(self._labels)
        icon.update_menu()
        if not icon.visible:
            icon.visible = True

    def notify(self, message: str) -> None:
        """A balloon from the icon."""
        if self._icon is not None:
            self._icon.notify(message, NOTIFICATION_TITLE)

    def stop(self) -> None:
        """Ask pystray's loop to end (it posts itself a message, so any thread will do)."""
        self._stop_requested.set()
        if self._icon is not None:
            self._icon.stop()


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
        # Overrides pystray's private `_assert_icon_handle` and sets its private
        # `_icon_handle`, which is why pystray is pinned to exactly 0.19.5. The
        # handle is declared here because pystray has no types: the win32 backend
        # sets it to None, then to LoadImage's HANDLE (c_void_p, so an int).
        _icon_handle: int | None
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
