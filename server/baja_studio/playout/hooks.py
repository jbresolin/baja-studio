"""The on-air webhook for BajaOvrLay: fire-and-forget POSTs from their own worker.

`{clip, started_at}` when a clip goes to air, `{clip, ended_at}` when it
leaves. Each POST has 2 s; a failure is logged at most once a minute, so a
dead overlay machine does not flood the log, and never delays a take.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2

from baja_studio.core.clock import Clock
from baja_studio.core.worker import WorkerContext

WEBHOOK_TIMEOUT_SECONDS = 2.0
FAILURE_LOG_INTERVAL_SECONDS = 60.0
IDLE_SLEEP_SECONDS = 1.0
# Clip fields an overlay can use; the file path stays private to the studio.
HOOK_CLIP_FIELDS = (
    "id",
    "title",
    "source",
    "prova",
    "car_number",
    "team",
    "passagem",
    "show_pass",
    "duration_s",
)


@dataclass(frozen=True)
class PendingPost:
    """One notification waiting to be sent."""

    url: str
    body: dict[str, Any]


def on_air_body(clip: dict[str, Any], started_at: str) -> dict[str, Any]:
    """The body POSTed when `clip` goes to air."""
    return {"clip": _hook_clip(clip), "started_at": started_at}


def off_air_body(clip: dict[str, Any], ended_at: str) -> dict[str, Any]:
    """The body POSTed when `clip` leaves the air."""
    return {"clip": _hook_clip(clip), "ended_at": ended_at}


class WebhookNotifier:
    """Queues notifications and sends them from the `hooks` worker."""

    def __init__(
        self,
        clock: Clock,
        logger: logging.LoggerAdapter[logging.Logger] | logging.Logger,
        transport: httpx2.BaseTransport | None = None,
        wake_worker: Callable[[], None] = lambda: None,
    ) -> None:
        """`transport` lets tests catch the POSTs; `wake_worker` nudges the worker."""
        self._clock = clock
        self._logger = logger
        self._transport = transport
        self.wake_worker = wake_worker
        self._lock = threading.Lock()
        self._pending: list[PendingPost] = []
        self._last_failure_logged_at: float | None = None

    def notify(self, url: str | None, body: dict[str, Any]) -> None:
        """Queue a POST to `url` (nothing happens when no URL is configured)."""
        if not url:
            return
        with self._lock:
            self._pending.append(PendingPost(url, body))
        self.wake_worker()

    def run(self, worker: WorkerContext) -> None:
        """Worker body: send whatever is queued, then sleep until woken."""
        with httpx2.Client(timeout=WEBHOOK_TIMEOUT_SECONDS, transport=self._transport) as client:
            while not worker.should_stop:
                self.send_pending(client)
                worker.sleep(IDLE_SLEEP_SECONDS)

    def send_pending(self, client: httpx2.Client) -> None:
        """POST every queued notification, oldest first."""
        with self._lock:
            pending, self._pending = self._pending, []
        for post in pending:
            try:
                response = client.post(post.url, json=post.body)
                if response.status_code >= 400:
                    self._log_failure(f"{post.url} answered {response.status_code}")
            except httpx2.HTTPError as error:
                self._log_failure(f"{post.url}: {error}")

    def _log_failure(self, message: str) -> None:
        now = self._clock.monotonic()
        last = self._last_failure_logged_at
        if last is None or now - last >= FAILURE_LOG_INTERVAL_SECONDS:
            self._last_failure_logged_at = now
            self._logger.warning("on-air webhook failed: %s", message)


def _hook_clip(clip: dict[str, Any]) -> dict[str, Any]:
    return {name: clip.get(name) for name in HOOK_CLIP_FIELDS}
