"""Settings export: every section of the settings store in one JSON file.

The file holds settings only, never operational state (queue, link states,
stored levels, played flags, command log: those live in other tables of the
database, and a copy of the database is their backup). Secrets are replaced
by `"<secret>"` and their places listed in `secrets_required`, so the file
can be mailed or kept in a shared folder:

    {
      "format": "baja-studio-settings",
      "format_version": 1,
      "app_version": "1.2.0",
      "exported_at": "2026-11-07T15:00:00+00:00",
      "hostname": "STREAM-PC",
      "secrets_required": ["gateway.tls.cloudflare_api_token", ...],
      "modules": {"cameras": {"settings_version": 1, "values": {...}}, ...}
    }

Paths name list items by their `id` when they have one (`agents.agents[laptop-a].token`),
otherwise by position (`[0]`).
"""

import re
import socket
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from baja_studio import __version__
from baja_studio.core.clock import Clock
from baja_studio.core.settings import (
    LIST_ITEM_KEY,
    SettingsStore,
    nested_model_fields,
    secret_field_names,
    settings_version_of,
)

FILE_FORMAT = "baja-studio-settings"
FILE_FORMAT_VERSION = 1
EXPORTED_SECRET = "<secret>"


def item_label(item: Any, index: int) -> str:
    """How a list item appears in a path: its id, or its position."""
    if isinstance(item, dict) and isinstance(item.get(LIST_ITEM_KEY), str):
        return str(item[LIST_ITEM_KEY])
    return str(index)


def secret_paths(model_class: type[BaseModel], values: dict[str, Any], prefix: str) -> list[str]:
    """Dotted paths of every non-empty secret in dumped `values`, at any depth."""
    found = [f"{prefix}.{name}" for name in secret_field_names(model_class) if values.get(name)]
    for name, (inner_class, is_list) in nested_model_fields(model_class).items():
        value = values.get(name)
        if is_list and isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    path = f"{prefix}.{name}[{item_label(item, index)}]"
                    found.extend(secret_paths(inner_class, item, path))
        elif isinstance(value, dict):
            found.extend(secret_paths(inner_class, value, f"{prefix}.{name}"))
    return found


def map_secrets(
    model_class: type[BaseModel], values: dict[str, Any], transform: Callable[[Any], Any]
) -> dict[str, Any]:
    """A copy of dumped `values` with every secret field, at any depth, passed through `transform`.

    `values` may come from a hand-edited file: anything that is not the shape
    the model expects is left as it is, for validation to report.
    """
    mapped = dict(values)
    for name in secret_field_names(model_class):
        if name in mapped:
            mapped[name] = transform(mapped[name])
    for name, (inner_class, is_list) in nested_model_fields(model_class).items():
        value = mapped.get(name)
        if is_list and isinstance(value, list):
            mapped[name] = [
                map_secrets(inner_class, item, transform) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            mapped[name] = map_secrets(inner_class, value, transform)
    return mapped


def replace_secrets(
    model_class: type[BaseModel], values: dict[str, Any], replacement: str
) -> dict[str, Any]:
    """A copy of dumped `values` with every non-empty secret, at any depth, set to `replacement`."""
    return map_secrets(model_class, values, lambda value: replacement if value else value)


def export_document(settings: SettingsStore, clock: Clock, hostname: str) -> dict[str, Any]:
    """Every section's current settings, secrets replaced, as the exported file's content."""
    modules: dict[str, Any] = {}
    required: list[str] = []
    for section in settings.sections():
        record = settings.record(section)
        model_class = type(record.values)
        values = record.values.model_dump(mode="json")
        required.extend(secret_paths(model_class, values, section))
        modules[section] = {
            "settings_version": settings_version_of(model_class),
            "values": replace_secrets(model_class, values, EXPORTED_SECRET),
        }
    return {
        "format": FILE_FORMAT,
        "format_version": FILE_FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": clock.now_utc().isoformat(),
        "hostname": hostname,
        "secrets_required": required,
        "modules": modules,
    }


def export_file_name(hostname: str, clock: Clock) -> str:
    """`BajaStudio-settings-<hostname>-<YYYYMMDD-HHMMSS>.json` (the hostname made file-safe)."""
    safe_host = re.sub(r"[^A-Za-z0-9-]+", "-", hostname).strip("-") or "studio"
    stamp = clock.now_utc().strftime("%Y%m%d-%H%M%S")
    return f"BajaStudio-settings-{safe_host}-{stamp}.json"


def this_hostname() -> str:
    """The PC's name, as the exported file records it."""
    return socket.gethostname()
