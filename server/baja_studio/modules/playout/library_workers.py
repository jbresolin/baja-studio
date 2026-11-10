"""Playout's library workers: folder scanner, ffprobe/thumbnails, orchestrator sync.

- `scanner` walks the local folders every 5 s and updates the library.
- `probe` gives new files a duration, a frame rate and a thumbnail (cached
  in the data folder), one at a time; the scanner wakes it when files appear.
  "Rescan library" (`rescan`) forgets all of that and deletes the cached
  thumbnails, so every file is measured again.
- `sync` pulls the orchestrator's manifest every `poll_interval_s`. The first
  pull of a run is a full one; later pulls ask only for clips delivered
  since the newest one seen (minus a minute of overlap; merging is idempotent).
  An unreachable orchestrator is `degraded`; the last merged list stays.
"""

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

from baja_studio.core.clock import Clock
from baja_studio.core.worker import HealthState, WorkerContext
from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.playout.config import OrchestratorSettings, PlayoutSettings
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.orchestrator import OrchestratorClient, OrchestratorError
from baja_studio.playout.probe import ProbeError, Prober, thumbnail_at
from baja_studio.playout.scanner import SCAN_INTERVAL_SECONDS, scan_folders

PROBE_IDLE_SECONDS = 5.0
SYNC_IDLE_SECONDS = 2.0
SYNC_OVERLAP = timedelta(minutes=1)
THUMBNAIL_FOLDER_NAME = "playout-thumbnails"

# Builds the prober for the tools as resolved now (`playout/tools.py`).
MakeProber = Callable[[], Prober]


class LibraryWorkers:
    """The three loops that keep the clip library up to date."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], PlayoutSettings],
        library: LibraryStore,
        publisher: PlayoutPublisher,
        data_directory: Path,
        make_prober: MakeProber,
        orchestrator_transport: httpx2.BaseTransport | None,
        logger: logging.LoggerAdapter[logging.Logger],
        wake_probe: Callable[[], None],
    ) -> None:
        """Everything the loops need; they start as supervisor workers."""
        self._clock = clock
        self._settings = settings
        self._library = library
        self._publisher = publisher
        self._thumbnail_folder = data_directory / THUMBNAIL_FOLDER_NAME
        self._make_prober = make_prober
        self._orchestrator_transport = orchestrator_transport
        self._logger = logger
        self._wake_probe = wake_probe

    def run_scanner(self, worker: WorkerContext) -> None:
        """Worker body: scan, apply, report unreadable folders, sleep 5 s."""
        while not worker.should_stop:
            sources = self._settings().sources
            scan = scan_folders(sources.local_folders, sources.receiver_root())
            changed = self._library.apply_scan(scan.files)
            self._publisher.publish_clips(changed)
            self._publisher.publish_folder_errors(scan.errors)
            if scan.errors:
                problems = "; ".join(f"{path}: {error}" for path, error in scan.errors.items())
                worker.report_health(HealthState.DEGRADED, f"cannot read {problems}")
            else:
                worker.report_health(HealthState.OK)
            if changed:
                self._wake_probe()
            worker.sleep(SCAN_INTERVAL_SECONDS)

    def run_probe(self, worker: WorkerContext) -> None:
        """Worker body: probe files without a duration, one by one; idle when none are left."""
        while not worker.should_stop:
            pending = self._library.needing_probe()
            for clip in pending:
                if worker.should_stop:
                    return
                self.probe_one(clip)
            if not pending:
                worker.sleep(PROBE_IDLE_SECONDS)

    def rescan(self) -> int:
        """Forget durations, frame rates, thumbnails and failures, then measure again.

        Cached thumbnails are deleted (a file the page still asks for answers
        404 until it is made again). Returns how many present files will be
        measured; the scanner and the prober are woken by the caller.
        """
        changed = self._library.forget_probe_results()
        if self._thumbnail_folder.is_dir():
            for thumbnail in self._thumbnail_folder.iterdir():
                # A file the OS still holds open (Windows) is simply made again later.
                with contextlib.suppress(OSError):
                    thumbnail.unlink()
        self._publisher.publish_clips(changed)
        self._publisher.publish_library_summary()
        self._wake_probe()
        return int(self._library.summary()["pending_probe"])

    def probe_one(self, clip: dict[str, Any]) -> None:
        """Duration (required), frame rate and thumbnail (best effort) for one clip."""
        prober = self._make_prober()
        try:
            info = prober.probe(clip["file_path"])
        except ProbeError as error:
            self._logger.warning("cannot probe %s: %s", clip["file_path"], error)
            self._library.set_probe_result(clip["id"], None, None, str(error))
            self._publisher.publish_clips([clip["id"]])
            return
        thumbnail = self._thumbnail_folder / f"{clip['id']}-{int(clip['mtime'] or 0)}.jpg"
        try:
            prober.make_thumbnail(clip["file_path"], thumbnail, thumbnail_at(info.duration_s))
            thumbnail_path: str | None = str(thumbnail)
        except ProbeError as error:
            self._logger.warning("no thumbnail for %s: %s", clip["file_path"], error)
            thumbnail_path = None
        self._library.set_probe_result(clip["id"], info.duration_s, thumbnail_path, None, info.fps)
        self._publisher.publish_clips([clip["id"]])

    def run_sync(self, worker: WorkerContext) -> None:
        """Worker body: pull the manifest and merge it, every poll interval."""
        client: OrchestratorClient | None = None
        client_settings: OrchestratorSettings | None = None
        newest_delivery: datetime | None = None
        last_success_at: str | None = None
        try:
            while not worker.should_stop:
                settings = self._settings().sources
                orchestrator = settings.orchestrator
                if not orchestrator.enabled or not orchestrator.base_url:
                    self._publisher.publish_sync(_sync_status("disabled", last_success_at))
                    worker.report_health(HealthState.OK)
                    worker.sleep(SYNC_IDLE_SECONDS)
                    continue
                delivery = settings.receiver_root()
                if delivery is None:
                    message = (
                        "no delivery folder: add the receiver's clip root in the playout "
                        "settings and mark it as the receiver root"
                    )
                    self._publisher.publish_sync(_sync_status("error", last_success_at, message))
                    worker.report_health(HealthState.DEGRADED, message)
                    worker.sleep(orchestrator.poll_interval_s)
                    continue
                if client is None or client_settings != orchestrator:
                    if client is not None:
                        client.close()
                    client = OrchestratorClient(
                        orchestrator.base_url, orchestrator.token, self._orchestrator_transport
                    )
                    client_settings, newest_delivery = orchestrator, None
                since = newest_delivery - SYNC_OVERLAP if newest_delivery else None
                try:
                    manifest = client.fetch_manifest(since)
                except OrchestratorError as error:
                    self._publisher.publish_sync(
                        _sync_status("unreachable", last_success_at, str(error))
                    )
                    worker.report_health(HealthState.DEGRADED, f"orchestrator: {error}")
                    worker.sleep(orchestrator.poll_interval_s)
                    continue
                changed = self._library.merge_manifest(
                    [entry.as_manifest_clip() for entry in manifest.clips],
                    delivery.path,
                    delivery.label or Path(delivery.path).name,
                )
                self._publisher.publish_clips(changed)
                if manifest.clips:
                    newest = max(entry.delivered_at for entry in manifest.clips)
                    newest_delivery = max(newest, newest_delivery or newest)
                last_success_at = self._clock.now_utc().isoformat()
                self._publisher.publish_sync(_sync_status("ok", last_success_at))
                worker.report_health(HealthState.OK)
                worker.sleep(orchestrator.poll_interval_s)
        finally:
            if client is not None:
                client.close()


def _sync_status(
    state: str, last_success_at: str | None, error: str | None = None
) -> dict[str, Any]:
    return {"state": state, "last_success_at": last_success_at, "error": error}
