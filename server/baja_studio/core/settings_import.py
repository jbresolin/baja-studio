"""Settings import: check an exported file against this install, then apply it.

Two steps, so nothing changes by surprise:

1. `plan_import(content, settings)`, the dry run: per section, the keys the
   file adds, removes or changes (validated against this install's models),
   and a confirmation token tied to the file *and* to the settings as they are now.
2. `apply_import(plan, settings, apply_partial)`: saves each changed section
   through `SettingsStore.update`, so subscribers react as to any edit and
   nothing needs a restart. The caller re-plans first and compares tokens:
   if the settings changed since the dry run, the operator must look again.

The whole file is refused when it is not a settings file or comes from a
newer MAJOR version of the studio. Otherwise sections are judged one by one:
an unknown section is skipped with a warning; one that does not validate (or
has a newer `settings_version`) is skipped with the reason, and the rest are
applied only if the operator ticks "apply partial". A secret the file carries
as `"<secret>"` keeps this install's value; a real value replaces it.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from baja_studio import __version__
from baja_studio.core.settings import (
    SECRET_MASK,
    SettingsConflictError,
    SettingsStore,
    restore_masked_secrets,
    settings_version_of,
)
from baja_studio.core.settings_export import (
    EXPORTED_SECRET,
    FILE_FORMAT,
    item_label,
    map_secrets,
    secret_paths,
)

MAX_SHOWN_INPUT = 40


class SettingsFileError(Exception):
    """The whole file is refused; the message says why in operator language."""


@dataclass(frozen=True)
class SettingsFile:
    """The parts of an exported file the import uses."""

    app_version: str
    exported_at: str
    hostname: str
    secrets_required: list[str]
    modules: dict[str, dict[str, Any]]


def major_version(version: str) -> int | None:
    """The MAJOR number of "1.2.3", "v1.2.3-rc.1" or "0.1.0.dev5"; None if there is none."""
    match = re.match(r"^\s*v?(\d+)", version)
    return int(match.group(1)) if match else None


def parse_settings_file(content: bytes, our_version: str = __version__) -> SettingsFile:
    """Read an exported file; raises `SettingsFileError` when it cannot be imported at all."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise SettingsFileError("this is not a settings file: it is not text") from None
    except json.JSONDecodeError as error:
        raise SettingsFileError(
            f"this is not a settings file: the JSON is broken at line {error.lineno} ({error.msg})"
        ) from None
    if not isinstance(document, dict) or document.get("format") != FILE_FORMAT:
        raise SettingsFileError("this is not a Baja Studio settings file (use Export to make one)")
    app_version = str(document.get("app_version", ""))
    theirs, ours = major_version(app_version), major_version(our_version)
    if theirs is None:
        raise SettingsFileError("the file does not say which version of the studio made it")
    if ours is not None and theirs > ours:
        raise SettingsFileError(
            f"the file was exported by Baja Studio {app_version}, newer than this one "
            f"({our_version}); upgrade this PC first"
        )
    modules = document.get("modules")
    if not isinstance(modules, dict) or not all(
        isinstance(entry, dict) and isinstance(entry.get("values"), dict)
        for entry in modules.values()
    ):
        raise SettingsFileError("the file's 'modules' part is damaged")
    required = document.get("secrets_required")
    return SettingsFile(
        app_version=app_version,
        exported_at=str(document.get("exported_at", "")),
        hostname=str(document.get("hostname", "")),
        secrets_required=[str(path) for path in required] if isinstance(required, list) else [],
        modules=modules,
    )


@dataclass(frozen=True)
class KeyChange:
    """One key of a section the import touches; secrets are shown as `"<secret>"`."""

    key: str
    old: Any
    new: Any
    secret: bool

    def as_state(self) -> dict[str, Any]:
        """JSON-ready, for the dry run's answer."""
        return {"key": self.key, "old": self.old, "new": self.new, "secret": self.secret}


@dataclass
class SectionPlan:
    """What the import would do to one section."""

    section: str
    # "changes", "unchanged", "invalid" (skipped unless partial) or "unknown" (skipped).
    status: str
    messages: list[str] = field(default_factory=list)
    file_settings_version: int | None = None
    current_settings_version: int | None = None
    added: list[KeyChange] = field(default_factory=list)
    removed: list[KeyChange] = field(default_factory=list)
    changed: list[KeyChange] = field(default_factory=list)
    secrets_to_enter: list[str] = field(default_factory=list)
    # What `apply_import` saves, and the store version it was planned against.
    patch: dict[str, Any] = field(default_factory=dict)
    base_version: int | None = None

    def as_state(self) -> dict[str, Any]:
        """JSON-ready (without the patch, which may hold real secrets)."""
        return {
            "section": self.section,
            "status": self.status,
            "messages": self.messages,
            "file_settings_version": self.file_settings_version,
            "current_settings_version": self.current_settings_version,
            "added": [change.as_state() for change in self.added],
            "removed": [change.as_state() for change in self.removed],
            "changed": [change.as_state() for change in self.changed],
            "secrets_to_enter": self.secrets_to_enter,
        }


@dataclass(frozen=True)
class ImportPlan:
    """The dry run: the file, what it does to each section, and the confirmation token."""

    file: SettingsFile
    sections: list[SectionPlan]
    token: str

    @property
    def blocked_sections(self) -> list[SectionPlan]:
        """Sections that cannot be applied; the rest go ahead only with "apply partial"."""
        return [plan for plan in self.sections if plan.status == "invalid"]

    def as_state(self) -> dict[str, Any]:
        """The dry run's answer to the page."""
        return {
            "token": self.token,
            "app_version": self.file.app_version,
            "exported_at": self.file.exported_at,
            "hostname": self.file.hostname,
            "sections": [plan.as_state() for plan in self.sections],
            "needs_partial": bool(self.blocked_sections),
            "has_changes": any(plan.status == "changes" for plan in self.sections),
        }


def plan_import(content: bytes, settings: SettingsStore) -> ImportPlan:
    """The dry run; raises `SettingsFileError` when the file is refused as a whole."""
    parsed = parse_settings_file(content)
    known = settings.sections()
    plans = [
        _plan_section(section, entry, settings, parsed.secrets_required)
        if section in known
        else SectionPlan(
            section,
            "unknown",
            [f"this studio has no module {section!r}; it is skipped"],
        )
        for section, entry in parsed.modules.items()
    ]
    versions = {section: settings.record(section).version for section in known}
    fingerprint = content + json.dumps(versions, sort_keys=True).encode("utf-8")
    return ImportPlan(parsed, plans, hashlib.sha256(fingerprint).hexdigest()[:16])


def apply_import(
    plan: ImportPlan, settings: SettingsStore, apply_partial: bool
) -> list[dict[str, str]]:
    """Save every section with changes; one `{section, result}` per section of the file.

    Refuses (`SettingsFileError`) when a section cannot be applied and
    `apply_partial` is off, before anything is saved.
    """
    blocked = plan.blocked_sections
    if blocked and not apply_partial:
        names = ", ".join(section.section for section in blocked)
        raise SettingsFileError(
            f"{names} cannot be imported (see the dry run); tick 'apply partial' to import the rest"
        )
    results = []
    for section in plan.sections:
        if section.status == "changes":
            try:
                settings.update(section.section, section.patch, section.base_version)
                outcome = "applied"
            except SettingsConflictError:
                outcome = "skipped: someone saved this section meanwhile; run the dry run again"
            except ValidationError as invalid:
                outcome = f"skipped: {readable_errors(invalid)}"
        elif section.status == "unchanged":
            outcome = "unchanged"
        else:
            outcome = "skipped: " + "; ".join(section.messages)
        results.append({"section": section.section, "result": outcome})
    return results


def readable_errors(invalid: ValidationError) -> str:
    """Pydantic's errors as "obs.port: Input should be a valid integer (got 'abc')"."""
    problems = []
    for problem in invalid.errors():
        where = ".".join(str(part) for part in problem["loc"]) or "(whole section)"
        shown = repr(problem.get("input"))
        if len(shown) > MAX_SHOWN_INPUT:
            shown = shown[: MAX_SHOWN_INPUT - 1] + "…"
        problems.append(f"{where}: {problem['msg']} (got {shown})")
    return "; ".join(problems)


def _plan_section(
    section: str, entry: dict[str, Any], settings: SettingsStore, file_secrets: list[str]
) -> SectionPlan:
    model_class = settings.model_class(section)
    record = settings.record(section)
    ours = settings_version_of(model_class)
    theirs = entry.get("settings_version", 1)
    plan = SectionPlan(
        section,
        "unchanged",
        file_settings_version=theirs if isinstance(theirs, int) else None,
        current_settings_version=ours,
        base_version=record.version,
    )
    if not isinstance(theirs, int) or theirs > ours:
        plan.status = "invalid"
        plan.messages.append(
            f"exported with a newer settings layout ({theirs}; this studio has {ours}): "
            "upgrade the studio first"
        )
        return plan
    if theirs < ours:
        plan.messages.append(
            f"exported with an older settings layout ({theirs}; this studio has {ours}): "
            "check the changes below"
        )
    values: dict[str, Any] = entry["values"]
    ignored = sorted(key for key in values if key not in model_class.model_fields)
    if ignored:
        plan.messages.append(f"keys this studio does not know are ignored: {', '.join(ignored)}")
    patch = map_secrets(
        model_class,
        {key: value for key, value in values.items() if key in model_class.model_fields},
        # "<secret>" (or the API's mask) means: keep what this PC has.
        lambda value: SECRET_MASK if value in (EXPORTED_SECRET, SECRET_MASK) else value,
    )
    current = record.values.model_dump()
    try:
        new_values = model_class.model_validate(
            {**current, **restore_masked_secrets(model_class, patch, current)}
        )
    except ValidationError as invalid:
        plan.status = "invalid"
        plan.messages.append(readable_errors(invalid))
        return plan
    plan.patch = patch
    _fill_diff(plan, model_class, record.values, new_values)
    still_secret = set(secret_paths(model_class, new_values.model_dump(mode="json"), section))
    plan.secrets_to_enter = [
        path for path in file_secrets if path.startswith(f"{section}.") and path not in still_secret
    ]
    plan.status = "changes" if plan.added or plan.removed or plan.changed else "unchanged"
    return plan


def _fill_diff(
    plan: SectionPlan, model_class: type[BaseModel], current: BaseModel, new: BaseModel
) -> None:
    """Added, removed and changed keys between this PC's values and the imported ones.

    A key is "added" when this PC does not have it (a new camera's fields) or
    has only the default there, so a fresh install shows the file as added.
    """
    section = plan.section
    current_json, new_json = current.model_dump(mode="json"), new.model_dump(mode="json")
    secrets = set(secret_paths(model_class, current_json, section)) | set(
        secret_paths(model_class, new_json, section)
    )
    old_flat, new_flat = flatten(current_json), flatten(new_json)
    try:
        defaults = flatten(model_class().model_dump(mode="json"))
    except ValidationError:
        defaults = {}

    def shown(key: str, value: Any) -> Any:
        return EXPORTED_SECRET if f"{section}.{key}" in secrets and value else value

    for key, value in new_flat.items():
        secret = f"{section}.{key}" in secrets
        if key not in old_flat:
            plan.added.append(KeyChange(key, None, shown(key, value), secret))
        elif old_flat[key] != value:
            change = KeyChange(key, shown(key, old_flat[key]), shown(key, value), secret)
            is_default = key in defaults and defaults[key] == old_flat[key]
            (plan.added if is_default else plan.changed).append(change)
    for key, value in old_flat.items():
        if key not in new_flat:
            secret = f"{section}.{key}" in secrets
            plan.removed.append(KeyChange(key, shown(key, value), None, secret))


def flatten(value: Any, path: str = "") -> dict[str, Any]:
    """Dumped settings as {dotted path: scalar}; list items by id or position.

    Empty lists and objects leave no key: a list emptied shows as its items removed.
    """
    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            flat.update(flatten(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flat.update(flatten(item, f"{path}[{item_label(item, index)}]"))
    else:
        flat[path] = value
    return flat
