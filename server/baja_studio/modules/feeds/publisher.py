"""Publishes `state.feeds` after each poll: the service, the cards, refusals, recordings.

The worker (in `__init__.py`) asks MediaMTX; this turns the answer, or its
absence, into what the Feeds page and the hosts of the readiness checks
read. Nothing here talks to MediaMTX itself.
"""

from pathlib import Path

from baja_studio.core.module import ModuleContext
from baja_studio.core.system_services import FEEDS_SERVICE, NOT_INSTALLED, ServiceControl
from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.control_api import MediamtxInfo, PathSnapshot
from baja_studio.modules.feeds.feed_status import FeedTracker
from baja_studio.modules.feeds.mediamtx_config import recordings_root
from baja_studio.modules.feeds.ports import FeedPortHolder, feed_ports
from baja_studio.modules.feeds.service_log import TAIL_LINES, log_path, refused_senders
from baja_studio.modules.feeds.system import FeedsSystem
from baja_studio.modules.feeds.urls import sender_addresses

# `netstat` and `tasklist` take a moment each: while MediaMTX is down, ask again only this often.
PORT_RECHECK_SECONDS = 30.0


class FeedsStatePublisher:
    """Writes the feeds module's state subtree; one per module start."""

    def __init__(
        self, context: ModuleContext, system: FeedsSystem, service_control: ServiceControl
    ) -> None:
        """Nothing published yet; the tracker remembers bitrates between polls."""
        self._context = context
        self._system = system
        self._service_control = service_control
        self._tracker = FeedTracker()
        self._holders: list[FeedPortHolder] = []
        self._holders_checked_at: float | None = None

    def publish_down(self, settings: FeedsSettings, error: str) -> str:
        """MediaMTX is not answering: say why (a port, the service, its log); returns the reason."""
        status = self._service_control.status(FEEDS_SERVICE)
        holders = self._port_holders_while_down(settings)
        if holders:
            reason = "; ".join(holder.message for holder in holders)
        elif status == NOT_INSTALLED:
            reason = f"MediaMTX is not answering, and the feeds service is not installed ({error})"
        elif status != "SERVICE_RUNNING":
            reason = f"the feeds service is {status.removeprefix('SERVICE_').lower()}"
        else:
            reason = f"MediaMTX is not answering ({error})"
        lines = self._system.read_log(log_path(self._context.data_directory))
        self._publish_service(settings, status, None, reason, holders, lines[-TAIL_LINES:])
        self._publish_feeds(settings, None, None)
        return reason

    def publish_up(
        self,
        settings: FeedsSettings,
        info: MediamtxInfo,
        snapshots: list[PathSnapshot],
        waiting_for_reload: bool,
    ) -> None:
        """MediaMTX answered: the service line, every card, and refused senders from its log."""
        self._holders_checked_at = None
        status = self._service_control.status(FEEDS_SERVICE)
        self._publish_service(settings, status, info.version, "", [], [])
        self._context.update_state("config.waiting_for_reload", waiting_for_reload)
        self._publish_feeds(settings, snapshots, info.started)
        lines = self._system.read_log(log_path(self._context.data_directory))
        known = {feed.name for feed in settings.feeds}
        refused = [{"name": item.name, "at": item.at} for item in refused_senders(lines, known)]
        self._context.update_state("refused", refused)

    def _publish_service(
        self,
        settings: FeedsSettings,
        status: str,
        version: str | None,
        message: str,
        holders: list[FeedPortHolder],
        log_tail: list[str],
    ) -> None:
        self._context.update_state(
            "service",
            {
                "name": FEEDS_SERVICE,
                "status": status,
                "answering": version is not None,
                "version": version,
                "api": f"127.0.0.1:{settings.api_port}",
                "message": message,
                "port_conflicts": [holder.as_state() for holder in holders],
                "log_tail": log_tail,
            },
        )

    def _port_holders_while_down(self, settings: FeedsSettings) -> list[FeedPortHolder]:
        now = self._context.clock.monotonic()
        due = self._holders_checked_at is None or (
            now - self._holders_checked_at >= PORT_RECHECK_SECONDS
        )
        if due:
            self._holders_checked_at = now
            self._holders = self._system.port_holders(feed_ports(settings))
        return self._holders

    def _publish_feeds(
        self, settings: FeedsSettings, snapshots: list[PathSnapshot] | None, started: str | None
    ) -> None:
        clock = self._context.clock
        addresses = sender_addresses(settings, self._system.list_addresses())
        cards = self._tracker.feed_cards(
            settings, snapshots, addresses, clock.monotonic(), clock.now_utc().isoformat(), started
        )
        self._context.update_state("addresses", addresses)
        self._context.update_state("feeds", cards)
        others = self._tracker.other_live_paths(settings, snapshots or [], clock.monotonic())
        self._context.update_state("other_paths", others)
        self._publish_recordings(settings)

    def _publish_recordings(self, settings: FeedsSettings) -> None:
        folder = recordings_root(settings, self._context.data_directory)
        recording = any(feed.record for feed in settings.feeds)
        free = self._free_bytes(folder) if recording else None
        self._context.update_state(
            "recordings", {"folder": str(folder), "free_bytes": free, "any_feed_records": recording}
        )

    def _free_bytes(self, folder: Path) -> int | None:
        try:
            return self._system.free_disk_bytes(folder)
        except OSError:
            return None
