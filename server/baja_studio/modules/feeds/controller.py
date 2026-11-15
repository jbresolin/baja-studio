"""Applies the feeds settings to MediaMTX: check ports, write the file, check it was loaded.

MediaMTX reloads `mediamtx.yml` by itself when it changes, so applying is
writing the file, with three precautions learned from the real binary
(v1.21.3, ADR 0022):

1. **A busy port stops MediaMTX** (it exits on the reload, and every feed
   with it). Ports that the new config adds are checked first; if another
   program holds one, nothing is written and the settings page says who.
2. **A second change within a second of a reload is ignored** by MediaMTX's
   file watcher. So writes are at least `MIN_WRITE_GAP_SECONDS` apart (the
   worker holds a change back until then), and
3. **every write is checked**: the file carries a fingerprint of its content
   (`sysLogPrefix`), and the running config's fingerprint must match it
   within `VERIFY_SECONDS`. If it does not, the file is written again; after
   `MAX_REWRITES` tries the feeds service is restarted, which reads the file
   afresh. Nothing else ever restarts it: feeds keep going.

Only the worker thread calls `apply_if_needed` and `verify`, so there is
one writer and no lock.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.system_services import (
    FEEDS_SERVICE,
    NOT_INSTALLED,
    ServiceControl,
    ServiceControlError,
)
from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.mediamtx_config import (
    FINGERPRINT_FIELD,
    config_path,
    parse_config,
    render_for_data_directory,
    write_config,
)
from baja_studio.modules.feeds.ports import FeedPort, PortHolderFinder, feed_ports

MIN_WRITE_GAP_SECONDS = 2.0
VERIFY_SECONDS = 4.0
MAX_REWRITES = 2


class FeedsConfigError(Exception):
    """The config cannot be applied as it is (a port is taken); the message says what to do."""


@dataclass
class _PendingCheck:
    fingerprint: str
    written_at: float
    rewrites: int = 0


class FeedsConfigController:
    """Owns `mediamtx.yml`: writes it when the settings change, and checks MediaMTX took it."""

    def __init__(
        self,
        data_directory: Path,
        service_control: ServiceControl,
        port_holders: PortHolderFinder,
        clock: Clock,
        logger: logging.LoggerAdapter[logging.Logger] | logging.Logger,
    ) -> None:
        """Nothing written yet in this run."""
        self._data_directory = data_directory
        self._service_control = service_control
        self._port_holders = port_holders
        self._clock = clock
        self._logger = logger
        self._last_write_at: float | None = None
        self._pending: _PendingCheck | None = None

    @property
    def path(self) -> Path:
        """The file MediaMTX reads."""
        return config_path(self._data_directory)

    def seconds_until_write_allowed(self) -> float:
        """0 when a write may happen now; otherwise how long the worker should wait."""
        if self._last_write_at is None:
            return 0.0
        return max(0.0, MIN_WRITE_GAP_SECONDS - (self._clock.monotonic() - self._last_write_at))

    def apply_if_needed(self, settings: FeedsSettings, mediamtx_on_api_port: bool) -> bool:
        """Write the config for `settings` if it differs from the file; True if written.

        Raises `FeedsConfigError` (and writes nothing) when a port the new
        config adds is taken. `mediamtx_on_api_port` says MediaMTX already
        answers on the new API port, so that port is its own, not a clash.
        Call only when `seconds_until_write_allowed()` is 0.
        """
        text = render_for_data_directory(settings, self._data_directory)
        current = self._current_text()
        if current == text:
            return False
        self._check_new_ports(settings, current, mediamtx_on_api_port)
        self._write(text)
        return True

    def verify(self, running_global_config: dict[str, Any]) -> str:
        """Compare what MediaMTX runs with the last write; returns a problem, or "".

        Called at every poll while MediaMTX answers. Writes again, or
        restarts the service, when MediaMTX missed the change for too long.
        """
        if self._pending is None:
            return ""
        running = running_global_config.get(FINGERPRINT_FIELD)
        if running == self._pending.fingerprint:
            self._pending = None
            return ""
        waited = self._clock.monotonic() - self._pending.written_at
        if waited < VERIFY_SECONDS:
            return ""
        if self._pending.rewrites < MAX_REWRITES:
            self._logger.warning(
                "MediaMTX did not reload its config after %.0f s; writing it again", waited
            )
            rewrites = self._pending.rewrites + 1
            self._write(self.path.read_text(encoding="utf-8"), force=True)
            assert self._pending is not None
            self._pending.rewrites = rewrites
            return ""
        return self._restart_service()

    @property
    def waiting_for_reload(self) -> bool:
        """True between a write and MediaMTX confirming it runs that config."""
        return self._pending is not None

    def _restart_service(self) -> str:
        assert self._pending is not None
        self._pending.written_at = self._clock.monotonic()
        self._pending.rewrites = 0
        if self._service_control.status(FEEDS_SERVICE) == NOT_INSTALLED:
            return (
                "MediaMTX is not running the config the studio wrote (no service to restart here)"
            )
        try:
            self._service_control.restart(FEEDS_SERVICE)
        except ServiceControlError as error:
            return f"MediaMTX did not reload its config, and restarting it failed: {error}"
        self._logger.warning("MediaMTX did not reload its config; feeds service restarted")
        return "MediaMTX did not reload its config: the feeds service was restarted"

    def _current_text(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _check_new_ports(
        self, settings: FeedsSettings, current_text: str | None, mediamtx_on_api_port: bool
    ) -> None:
        """Refuse when a port the new config opens (and the old did not) is taken."""
        wanted = feed_ports(settings)
        held_now = _ports_in(current_text)
        if mediamtx_on_api_port:
            held_now.add(("TCP", settings.api_port))
        new = [port for port in wanted if (port.protocol, port.port) not in held_now]
        holders = self._port_holders(new) if new else []
        if holders:
            raise FeedsConfigError("; ".join(holder.message for holder in holders))

    def _write(self, text: str, force: bool = False) -> None:
        # `force`: the same content again, but a new file, which MediaMTX's watcher sees.
        write_config(self.path, text, force=force)
        now = self._clock.monotonic()
        self._last_write_at = now
        fingerprint = str(parse_config(text).get(FINGERPRINT_FIELD, ""))
        self._pending = _PendingCheck(fingerprint, now)


def _ports_in(text: str | None) -> set[tuple[str, int]]:
    """The (protocol, port) pairs a written config opens, to tell which ports are new."""
    if text is None:
        return set()
    try:
        document = parse_config(text)
    except ValueError:
        return set()
    found = set()
    for key, protocol in (
        ("rtmpAddress", "TCP"),
        ("srtAddress", "UDP"),
        ("rtspAddress", "TCP"),
        ("apiAddress", "TCP"),
    ):
        port_text = str(document.get(key, "")).rpartition(":")[2]
        if port_text.isdigit():
            found.add((protocol, int(port_text)))
    return found


__all__ = ["FeedPort", "FeedsConfigController", "FeedsConfigError"]
