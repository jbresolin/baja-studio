"""Typed, versioned settings for every module, persisted in SQLite.

Each module declares a Pydantic model; the store keeps one JSON document per
module in the `settings` table. Every successful update bumps a version number
so two browsers editing at once cannot silently overwrite each other.
"""

import json
import logging
import threading
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database

logger = logging.getLogger(__name__)

SECRET_MASK = "********"


class ModuleSettings(BaseModel):
    """Base class for every module's settings model; adds the `enabled` switch."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(
        default=True, description="When off, the module is stopped and shows as 'stopped'."
    )


class UnknownSettingsSectionError(KeyError):
    """Raised when asking for settings of a module that never registered."""


class SettingsConflictError(Exception):
    """Raised when a caller saves on top of a version that is no longer current."""

    def __init__(self, section: str, expected_version: int, current_version: int) -> None:
        """Remember both versions so the API can tell the browser what changed."""
        super().__init__(
            f"settings for {section!r} are at version {current_version}, "
            f"but the change was based on version {expected_version}"
        )
        self.section = section
        self.expected_version = expected_version
        self.current_version = current_version


@dataclass(frozen=True)
class SettingsRecord:
    """One module's settings together with their version and last change time."""

    section: str
    version: int
    values: BaseModel
    updated_at: str


SettingsCallback = Callable[[BaseModel], None]

# Items of a list of models are matched by this field when restoring masked secrets.
LIST_ITEM_KEY = "id"


def secret_field_names(model_class: type[BaseModel]) -> set[str]:
    """Names of fields marked `json_schema_extra={"secret": True}`."""
    names = set()
    for name, field in model_class.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("secret") is True:
            names.add(name)
    return names


def nested_model_fields(model_class: type[BaseModel]) -> dict[str, tuple[type[BaseModel], bool]]:
    """Fields holding another model (`Model`, `Model | None`) or a list of them.

    Maps field name to (inner model class, is a list). Secrets inside these
    are masked and restored just like top-level ones.
    """
    nested: dict[str, tuple[type[BaseModel], bool]] = {}
    for name, field in model_class.model_fields.items():
        annotation = field.annotation
        is_list = get_origin(annotation) is list
        if is_list:
            annotation = get_args(annotation)[0]
        if get_origin(annotation) in (Union, types.UnionType):
            options = [option for option in get_args(annotation) if option is not type(None)]
            annotation = options[0] if len(options) == 1 else None
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            nested[name] = (annotation, is_list)
    return nested


def mask_secrets(model_class: type[BaseModel], values: dict[str, Any]) -> dict[str, Any]:
    """A copy of dumped `values` with every non-empty secret, at any depth, masked."""
    masked = dict(values)
    for name in secret_field_names(model_class):
        if masked.get(name):
            masked[name] = SECRET_MASK
    for name, (inner_class, is_list) in nested_model_fields(model_class).items():
        value = masked.get(name)
        if is_list and isinstance(value, list):
            masked[name] = [
                mask_secrets(inner_class, item) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            masked[name] = mask_secrets(inner_class, value)
    return masked


def restore_masked_secrets(
    model_class: type[BaseModel], patch: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """`patch` with every secret sent back as the mask replaced by its current value.

    Items of a list of models are matched to the current list by their `id`
    field when they have one (so reordering or deleting items keeps the right
    secret with the right item), otherwise by position. A masked secret with
    nothing to match becomes empty rather than the literal mask.
    """
    restored = dict(patch)
    for name in secret_field_names(model_class):
        if restored.get(name) == SECRET_MASK:
            restored[name] = current.get(name, "")
    for name, (inner_class, is_list) in nested_model_fields(model_class).items():
        if name not in restored:
            continue
        value, current_value = restored[name], current.get(name)
        if is_list and isinstance(value, list):
            current_items = current_value if isinstance(current_value, list) else []
            restored[name] = [
                restore_masked_secrets(
                    inner_class, item, _matching_item(item, index, current_items)
                )
                if isinstance(item, dict)
                else item
                for index, item in enumerate(value)
            ]
        elif isinstance(value, dict):
            current_dict = current_value if isinstance(current_value, dict) else {}
            restored[name] = restore_masked_secrets(inner_class, value, current_dict)
    return restored


def _matching_item(item: dict[str, Any], index: int, current_items: list[Any]) -> dict[str, Any]:
    if LIST_ITEM_KEY in item:
        for candidate in current_items:
            if isinstance(candidate, dict) and candidate.get(LIST_ITEM_KEY) == item[LIST_ITEM_KEY]:
                return candidate
        return {}
    if index < len(current_items) and isinstance(current_items[index], dict):
        return dict(current_items[index])
    return {}


class SettingsStore:
    """Loads, validates, saves and announces changes to module settings."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Create an empty store; modules add themselves with `register`."""
        self._database = database
        self._clock = clock
        self._lock = threading.Lock()
        self._models: dict[str, type[BaseModel]] = {}
        self._records: dict[str, SettingsRecord] = {}
        self._subscribers: dict[str, list[SettingsCallback]] = {}

    def register(self, section: str, model_class: type[BaseModel]) -> BaseModel:
        """Declare a section's model and load its saved values (or defaults).

        Called once per module at startup. Saved keys the model no longer has
        are dropped; if the saved document no longer validates at all, the
        defaults are used and the problem is logged, so a bad row can never
        stop the service from starting.
        """
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT version, body, updated_at FROM settings WHERE module = ?", (section,)
            ).fetchone()
        if row is None:
            values = model_class()
            record = self._save(section, values, version=1)
        else:
            values = self._load_saved_values(section, model_class, json.loads(row["body"]))
            record = SettingsRecord(section, row["version"], values, row["updated_at"])
        with self._lock:
            self._models[section] = model_class
            self._records[section] = record
            self._subscribers.setdefault(section, [])
        return record.values

    def sections(self) -> list[str]:
        """Names of all registered sections, in registration order."""
        with self._lock:
            return list(self._models)

    def get(self, section: str) -> BaseModel:
        """Current validated settings for `section`."""
        return self.record(section).values

    def record(self, section: str) -> SettingsRecord:
        """Current settings for `section` with version and update time."""
        with self._lock:
            if section not in self._records:
                raise UnknownSettingsSectionError(section)
            return self._records[section]

    def schema(self, section: str) -> dict[str, Any]:
        """JSON Schema of the section's model, used to render the Settings form."""
        with self._lock:
            if section not in self._models:
                raise UnknownSettingsSectionError(section)
            model_class = self._models[section]
        return model_class.model_json_schema()

    def masked_values(self, section: str) -> dict[str, Any]:
        """Settings as a plain dict with secret values (at any depth) masked, for the API."""
        record = self.record(section)
        return mask_secrets(type(record.values), record.values.model_dump(mode="json"))

    def update(
        self, section: str, patch: dict[str, Any], expected_version: int | None = None
    ) -> BaseModel:
        """Apply `patch` on top of the current values, validate, save and notify.

        `expected_version` is the version the caller last saw; if someone else
        saved since, `SettingsConflictError` is raised and nothing changes.
        A secret field sent back as the mask means "unchanged", also inside
        nested models and lists of models (see `restore_masked_secrets`). Raises
        pydantic's `ValidationError` if the result is invalid.
        """
        with self._lock:
            if section not in self._records:
                raise UnknownSettingsSectionError(section)
            current = self._records[section]
            if expected_version is not None and expected_version != current.version:
                raise SettingsConflictError(section, expected_version, current.version)
            model_class = self._models[section]
            current_values = current.values.model_dump()
            effective_patch = restore_masked_secrets(model_class, patch, current_values)
            merged = {**current_values, **effective_patch}
            values = model_class.model_validate(merged)
            record = self._save(section, values, version=current.version + 1)
            self._records[section] = record
            subscribers = list(self._subscribers[section])
        self._notify(section, subscribers, values)
        return values

    def subscribe(self, section: str, callback: SettingsCallback) -> None:
        """Call `callback(new_values)` after every successful update of `section`.

        This is how a module reacts to edits without a restart. Callbacks run on
        the thread that saved, so they should be quick (e.g. wake a worker).
        """
        with self._lock:
            if section not in self._models:
                raise UnknownSettingsSectionError(section)
            self._subscribers[section].append(callback)

    def _save(self, section: str, values: BaseModel, version: int) -> SettingsRecord:
        updated_at = self._clock.now_utc().isoformat()
        body = values.model_dump_json()
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO settings (module, version, body, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(module) DO UPDATE SET version = excluded.version, "
                "body = excluded.body, updated_at = excluded.updated_at",
                (section, version, body, updated_at),
            )
        return SettingsRecord(section, version, values, updated_at)

    def _load_saved_values(
        self, section: str, model_class: type[BaseModel], saved: dict[str, Any]
    ) -> BaseModel:
        known_keys = {key: value for key, value in saved.items() if key in model_class.model_fields}
        try:
            return model_class.model_validate(known_keys)
        except ValidationError:
            logger.exception(
                "saved settings no longer valid; using defaults", extra={"studio_module": section}
            )
            return model_class()

    def _notify(self, section: str, subscribers: list[SettingsCallback], values: BaseModel) -> None:
        for callback in subscribers:
            try:
                callback(values)
            except Exception:
                # One broken subscriber must not stop the others hearing about the change.
                logger.exception("settings subscriber failed", extra={"studio_module": section})
