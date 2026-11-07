"""The diagnostics bundle: one zip with what is needed to understand a problem later.

Built when the home page's "Diagnostics bundle" button is pressed (the home
module's `diagnostics_bundle` command), into `<data>/diagnostics/`:

- `logs/<file>`: the last 5 MB of every file in the log folder (the service's
  own log, NSSM's captured output of both services);
- `settings.json` with secrets masked; `health.json`; `state.json` (the
  whole state tree); `commands/<module>.json` (the last 500 per module);
- `versions.json` (app, Python, Caddy, OS); `services.json` (NSSM's view);
- `Caddyfile`, the gateway config Caddy runs.

Never the SQLite file. And as a last guard, every secret value in the
settings is replaced by the mask wherever it appears, in any file.
"""

import json
import logging
import platform
import re
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baja_studio import __version__
from baja_studio.core.clock import Clock
from baja_studio.core.commandlog import CommandLog
from baja_studio.core.settings import SECRET_MASK, SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.system_services import GATEWAY_SERVICE, STUDIO_SERVICE, ServiceControl

logger = logging.getLogger(__name__)

DIAGNOSTICS_DIRECTORY_NAME = "diagnostics"
LOG_TAIL_BYTES = 5 * 1024 * 1024
COMMANDS_PER_MODULE = 500
BUNDLES_KEPT = 5
BUNDLE_NAME_PATTERN = re.compile(r"^BajaStudio-diag-\d{8}-\d{6}\.zip$")
CUT_MARKER = "[... earlier lines left out of the diagnostics bundle ...]\n"
# Shorter values could be ordinary words; no real token or password is this short.
SHORTEST_SCRUBBED_SECRET = 4


@dataclass(frozen=True)
class DiagnosticsSources:
    """What a bundle is built from: the shared services and where files are."""

    data_directory: Path
    log_directory: Path
    caddyfile: Path
    clock: Clock
    settings: SettingsStore
    state: StateStore
    command_log: CommandLog
    module_names: Callable[[], list[str]]
    health: Callable[[], dict[str, Any]]
    service_control: ServiceControl


def diagnostics_directory(data_directory: Path) -> Path:
    """Where bundles are written (and served from)."""
    return data_directory / DIAGNOSTICS_DIRECTORY_NAME


def build_bundle(
    sources: DiagnosticsSources,
    log_tail_bytes: int = LOG_TAIL_BYTES,
    commands_per_module: int = COMMANDS_PER_MODULE,
) -> Path:
    """Write a new bundle and return its path; older bundles beyond five are deleted."""
    members = _collect_members(sources, log_tail_bytes, commands_per_module)
    secrets = [
        value
        for value in sources.settings.all_secret_values()
        if len(value) >= SHORTEST_SCRUBBED_SECRET
    ]
    folder = diagnostics_directory(sources.data_directory)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = sources.clock.now_utc().strftime("%Y%m%d-%H%M%S")
    path = folder / f"BajaStudio-diag-{stamp}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, text in members.items():
            bundle.writestr(name, scrub(text, secrets))
    _delete_old_bundles(folder)
    logger.info("diagnostics bundle written to %s", path)
    return path


def scrub(text: str, secrets: list[str]) -> str:
    """`text` with every occurrence of each secret replaced by the mask."""
    # Longest first, so a secret that contains another is replaced whole.
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, SECRET_MASK)
    return text


def read_tail(path: Path, limit_bytes: int) -> str:
    """The last `limit_bytes` of a file, starting at a whole line, marked if cut."""
    with path.open("rb") as file:
        size = file.seek(0, 2)
        if size <= limit_bytes:
            file.seek(0)
            return file.read().decode("utf-8", errors="replace")
        file.seek(size - limit_bytes)
        data = file.read()
    # Drop the partial first line; NSSM's captured output may be any encoding.
    first_newline = data.find(b"\n")
    data = data[first_newline + 1 :] if first_newline >= 0 else data
    return CUT_MARKER + data.decode("utf-8", errors="replace")


def _collect_members(
    sources: DiagnosticsSources, log_tail_bytes: int, commands_per_module: int
) -> dict[str, str]:
    members: dict[str, str] = {}
    if sources.log_directory.is_dir():
        for log_file in sorted(sources.log_directory.iterdir()):
            if log_file.is_file():
                members[f"logs/{log_file.name}"] = read_tail(log_file, log_tail_bytes)
    settings = {
        section: sources.settings.masked_values(section) for section in sources.settings.sections()
    }
    members["settings.json"] = _json(settings)
    members["health.json"] = _json(sources.health())
    state_tree = sources.state.snapshot()["state"]
    members["state.json"] = _json(state_tree)
    for module in sources.module_names():
        entries = sources.command_log.tail(module=module, limit=commands_per_module)
        members[f"commands/{module}.json"] = _json(entries)
    members["versions.json"] = _json(
        {
            "app": __version__,
            "python": sys.version,
            "os": platform.platform(),
            # Published by the gateway module, which asks caddy.exe at start.
            "caddy": (state_tree.get("gateway") or {}).get("caddy", {}).get("version"),
        }
    )
    members["services.json"] = _json(
        {
            service: sources.service_control.status(service)
            for service in (STUDIO_SERVICE, GATEWAY_SERVICE)
        }
    )
    if sources.caddyfile.is_file():
        members["Caddyfile"] = sources.caddyfile.read_text(encoding="utf-8", errors="replace")
    return members


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=repr) + "\n"


def _delete_old_bundles(folder: Path) -> None:
    bundles = sorted(path for path in folder.iterdir() if BUNDLE_NAME_PATTERN.match(path.name))
    for old in bundles[:-BUNDLES_KEPT]:
        old.unlink(missing_ok=True)
