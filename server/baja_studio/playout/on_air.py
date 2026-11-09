"""On-air actions: commands sent to other modules when a clip starts or ends (WP5b §4).

The typical one runs an audio macro (mute the trackside mics while a clip
plays, open them again after). Actions are queued at the take and sent from
their own `actions` worker through the service's normal command path, with
source `playout:on_air`, so they are in the command log like any other
command. A failure (audio offline, a macro that no longer exists) is logged
and reported to the page as a notice; it never delays or blocks a take.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from baja_studio.core.worker import WorkerContext
from baja_studio.playout.config import OnAirAction, OnAirActions

ON_AIR_SOURCE = "playout:on_air"
IDLE_SLEEP_SECONDS = 1.0

Moment = Literal["on_clip_start", "on_clip_end"]
# (module, command, args, source) → result; the registry's `dispatch_command`.
Dispatch = Callable[[str, str, dict[str, Any], str], Any]
# Told about each failure, worded for the operator.
FailureListener = Callable[[str], None]


@dataclass(frozen=True)
class PendingAction:
    """One action waiting to be sent, with the clip it is for (for the messages)."""

    action: OnAirAction
    moment: Moment
    clip_title: str


def actions_for(
    moment: Moment, settings_actions: OnAirActions, playlist_override: OnAirActions | None
) -> list[OnAirAction]:
    """The actions to run at `moment`: the playlist's override wins over the settings."""
    chosen = playlist_override if playlist_override is not None else settings_actions
    return list(getattr(chosen, moment))


class OnAirActionRunner:
    """Queues on-air actions and sends them from the `actions` worker."""

    def __init__(
        self,
        dispatch: Dispatch | None,
        logger: logging.LoggerAdapter[logging.Logger] | logging.Logger,
        on_failure: FailureListener,
        wake_worker: Callable[[], None] = lambda: None,
    ) -> None:
        """`dispatch` is None when the service has no registry to send through (tests)."""
        self._dispatch = dispatch
        self._logger = logger
        self._on_failure = on_failure
        self.wake_worker = wake_worker
        self._lock = threading.Lock()
        self._pending: list[PendingAction] = []

    def queue(self, actions: list[OnAirAction], moment: Moment, clip_title: str) -> None:
        """Send these actions soon (called under the controller lock; never blocks)."""
        if not actions:
            return
        with self._lock:
            self._pending.extend(PendingAction(action, moment, clip_title) for action in actions)
        self.wake_worker()

    def run(self, worker: WorkerContext) -> None:
        """Worker body: send whatever is queued, then sleep until woken."""
        while not worker.should_stop:
            self.send_pending()
            worker.sleep(IDLE_SLEEP_SECONDS)

    def send_pending(self) -> None:
        """Send every queued action, oldest first; failures are logged and reported."""
        with self._lock:
            pending, self._pending = self._pending, []
        for item in pending:
            action = item.action
            what = f"{action.module}.{action.command}"
            if self._dispatch is None:
                self._fail(f"on-air action {what} not sent: no command path", item)
                continue
            try:
                self._dispatch(action.module, action.command, dict(action.args), ON_AIR_SOURCE)
            except Exception as error:
                self._fail(f"on-air action {what} failed: {error}", item)

    def _fail(self, message: str, item: PendingAction) -> None:
        moment = "start" if item.moment == "on_clip_start" else "end"
        self._logger.warning("%s (clip %s of %r)", message, moment, item.clip_title)
        self._on_failure(f"{message} (at the {moment} of {item.clip_title!r})")
