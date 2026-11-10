"""A stand-in for the notification-area icon, plugged into `run_tray` as its backend.

It behaves like pystray where it matters for Quit: `run` blocks the calling
thread (the "icon loop") until `stop`, calls `on_ready` on a separate setup
thread, and runs menu clicks on the loop thread, one at a time. It needs no
desktop, so these tests also run on the Windows CI runner.
"""

import queue
import threading
from collections.abc import Callable

from baja_studio_tray.menu import MenuEntry
from baja_studio_tray.status import TrayStatus

# No test should get anywhere near this; it only keeps a broken test from hanging.
LOOP_GIVES_UP_SECONDS = 10.0


class FakeIcon:
    """Records what the tray shows and lets a test click menu items."""

    def __init__(self) -> None:
        self.shown: list[TrayStatus] = []
        self.notifications: list[str] = []
        self.stop_calls = 0
        self.loop_thread: threading.Thread | None = None
        self.setup_thread: threading.Thread | None = None
        self.running = threading.Event()
        self.first_status = threading.Event()
        self._entries: list[MenuEntry] = []
        # Clicks (by the item's text) waiting for the loop; None ends the loop.
        self._clicks: queue.Queue[str | None] = queue.Queue()

    def run(self, entries: list[MenuEntry], on_ready: Callable[[], None]) -> None:
        self._entries = entries
        self.loop_thread = threading.current_thread()
        self.setup_thread = threading.Thread(target=on_ready, name="fake-setup")
        self.setup_thread.start()
        self.running.set()
        while True:
            text = self._clicks.get(timeout=LOOP_GIVES_UP_SECONDS)
            if text is None:
                break
            entry = next(entry for entry in self._entries if entry.text() == text)
            assert entry.action is not None, f"{text!r} cannot be clicked"
            entry.action()
        self.running.clear()
        self.setup_thread.join()

    def show(self, status: TrayStatus) -> None:
        self.shown.append(status)
        self.first_status.set()

    def notify(self, message: str) -> None:
        self.notifications.append(message)

    def stop(self) -> None:
        self.stop_calls += 1
        self._clicks.put(None)

    def click(self, text: str) -> None:
        """What a user's click does: the item's action runs on the icon loop."""
        self._clicks.put(text)
