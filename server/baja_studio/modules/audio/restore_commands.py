"""Audio setup → "Restore defaults…" for the macros and the link matrix (WP9b).

An existing install keeps its macros and link matrix when the defaults
change (both are written on the first start only). These commands bring
the defaults back through the page's draft, Save and Discard: a preview
changes nothing, and the page shows what would change before saving.

    preview_defaults {part: "macros", macros}     → {macros, changes: [{id, label, change}]}
    preview_defaults {part: "links"}              → {links, current, changes}
    restore_defaults {part: "macros", version, changes} → {version, macros}
    restore_defaults {part: "links", expected}    → {links, changes}

A link change is {channel, bus, before, after}, each a link state.

Macros: the draft's macros with a default id are replaced by the default,
missing defaults are added at the end, every other macro is left alone
(`change` is replaced / added / unchanged / custom). The page saves the
result with the usual settings save (a stale version is refused with 409)
and then sends `restore_defaults` with the change list, which checks the
saved setup carries the defaults.

Links: `restore_defaults` makes the matrix the seed (`defaults.LINKS`),
pauses included; `expected` is the matrix the preview was made from, and
a matrix changed since then is refused. Offsets are kept.

Both restores are in the command log under the page's client id
(`web:<client id>`); the previews, which change nothing, are not.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import AUX_BUSES, SEND_SOURCES
from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings, MacroConfig
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.controller import AudioController

RESTORE_COMMANDS = ("preview_defaults", "restore_defaults")


def default_macros() -> list[MacroConfig]:
    """The macros a fresh install starts with, validated as settings."""
    return AudioSettings().macros


def merge_default_macros(
    current: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """`current` with the default-id macros replaced and the missing defaults added."""
    fresh = {macro.id: macro.model_dump(mode="json") for macro in default_macros()}
    merged: list[dict[str, Any]] = []
    changes: list[dict[str, str]] = []
    for macro in current:
        macro_id = str(macro.get("id", ""))
        default = fresh.pop(macro_id, None)
        if default is None:
            merged.append(macro)
            changes.append(
                {"id": macro_id, "label": str(macro.get("label", "")), "change": "custom"}
            )
            continue
        same = _canonical(macro) == default
        merged.append(default)
        changes.append(
            {
                "id": macro_id,
                "label": default["label"],
                "change": "unchanged" if same else "replaced",
            }
        )
    for macro_id, default in fresh.items():
        merged.append(default)
        changes.append({"id": macro_id, "label": default["label"], "change": "added"})
    return merged, changes


def link_changes(
    current: dict[str, dict[str, str]], wanted: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """Every pair whose state differs between the two matrices, input by input."""
    changes = []
    for channel in SEND_SOURCES:
        for bus in AUX_BUSES:
            before = current.get(channel, {}).get(bus, "unlinked")
            after = wanted.get(channel, {}).get(bus, "unlinked")
            if before != after:
                changes.append({"channel": channel, "bus": bus, "before": before, "after": after})
    return changes


def run_restore_command(
    controller: AudioController, command: str, args: dict[str, Any], source: str
) -> Any:
    """Preview the defaults, or log a saved macro restore / restore the link matrix."""
    part = args.get("part")
    if part not in ("macros", "links"):
        raise CommandError("part must be 'macros' or 'links'")
    if command == "preview_defaults":
        if part == "links":
            current = controller.links.matrix()
            return {
                "links": defaults.LINKS,
                "current": current,
                "changes": link_changes(current, defaults.LINKS),
            }
        if not isinstance(args.get("macros"), list):
            raise CommandError("preview_defaults needs the draft's macros")
        macros, changes = merge_default_macros(args["macros"])
        return {"macros": macros, "changes": changes}
    if part == "links":
        return _restore_links(controller, args.get("expected"), source)
    return _note_macros_restored(controller, args.get("version"), source)


def _restore_links(controller: AudioController, expected: Any, source: str) -> dict[str, Any]:
    with controller.lock:
        current = controller.links.matrix()
        if expected != current:
            raise CommandError(
                "the link matrix changed since you pressed Restore defaults; press it again"
            )
        changes = link_changes(current, defaults.LINKS)
        for change in changes:
            controller.set_link(change["channel"], change["bus"], change["after"], source)
        return {"links": controller.links.matrix(), "changes": changes}


def _note_macros_restored(controller: AudioController, version: Any, source: str) -> dict[str, Any]:
    if not isinstance(version, int):
        raise CommandError("restore_defaults needs the version the restore was saved as")
    saved = {macro.id: macro for macro in controller.settings().macros}
    missing = [macro.id for macro in default_macros() if saved.get(macro.id) != macro]
    if missing:
        raise CommandError(
            "the saved audio setup does not carry the default macros "
            f"({', '.join(missing)}); restore and save again"
        )
    return {"version": version, "macros": [macro.id for macro in default_macros()]}


def _canonical(macro: dict[str, Any]) -> dict[str, Any] | None:
    """`macro` as the settings would store it, or None when it does not validate."""
    try:
        return MacroConfig.model_validate(macro).model_dump(mode="json")
    except ValueError:
        return None
