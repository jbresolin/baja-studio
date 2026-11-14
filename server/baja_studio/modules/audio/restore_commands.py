"""Audio setup → "Restore defaults…" for the macros and the link matrix (WP9b).

An existing install keeps its macros and link matrix when the defaults
change (both are written on the first start only). These commands bring
the defaults back through the page's draft, Save and Discard: a preview
changes nothing, and the page shows what would change before saving.

    preview_defaults {part: "macros", macros, settings?} → {macros, changes, released}
    preview_defaults {part: "links", settings?}   → {links, current, changes, released}
    restore_defaults {part: "macros", version, changes} → {version, macros, released}
    restore_defaults {part: "links", expected}    → {links, changes, released}

A link change is {channel, bus, before, after}, each a link state.

**The defaults follow the patch** (`audio/patch.py`): the seats' microphone
channels, the production microphones and the other inputs shown on the
audio page, from the page's draft (`settings`) in a preview and from the
saved settings in a restore. The studio's desk has the hosts on 5-8; the
2026 defaults took 5-8 for production microphones (2026-11-14 bench).

**Held levels are released.** A send a macro (or button) closed keeps a
stored level, and a send with a stored level ignores its fader. A level
held on a main-mix input's send (a seat's microphone or another input of
the main mix) that no macro holds in its current state is forgotten, and a
linked send goes back to its fader: otherwise a send an old macro closed
stays silent for good. `released` lists those points ("5:bus1").

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

from pydantic import ValidationError

from baja_studio.adapters.xr18_addresses import AUX_BUSES, SEND_SOURCES
from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings, MacroConfig, StoreLevelsStep
from baja_studio.audio.macros import is_active
from baja_studio.audio.patch import patch_of
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.controller import AudioController

RESTORE_COMMANDS = ("preview_defaults", "restore_defaults")


def default_macros(settings: AudioSettings | None = None) -> list[MacroConfig]:
    """The default macros for the patch in `settings` (None: a fresh install's)."""
    if settings is None:
        return AudioSettings().macros
    built = defaults.macros(patch_of(settings))
    return [MacroConfig.model_validate(macro) for macro in built]


def default_links(settings: AudioSettings) -> dict[str, dict[str, str]]:
    """The default link matrix for the patch in `settings`."""
    return defaults.links(patch_of(settings))


def merge_default_macros(
    current: list[dict[str, Any]], settings: AudioSettings | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """`current` with the default-id macros replaced and the missing defaults added."""
    fresh = {macro.id: macro.model_dump(mode="json") for macro in default_macros(settings)}
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
        settings = _draft_settings(controller, args.get("settings"))
        if part == "links":
            current = controller.links.matrix()
            wanted = default_links(settings)
            return {
                "links": wanted,
                "current": current,
                "changes": link_changes(current, wanted),
                "released": held_to_release(controller, settings, settings.macros),
            }
        if not isinstance(args.get("macros"), list):
            raise CommandError("preview_defaults needs the draft's macros")
        macros, changes = merge_default_macros(args["macros"], settings)
        merged = [MacroConfig.model_validate(macro) for macro in macros]
        return {
            "macros": macros,
            "changes": changes,
            "released": held_to_release(controller, settings, merged),
        }
    if part == "links":
        return _restore_links(controller, args.get("expected"), source)
    return _note_macros_restored(controller, args.get("version"), source)


def _draft_settings(controller: AudioController, draft: Any) -> AudioSettings:
    """The page's draft (unsaved Seats included) if it sent one, else the saved settings."""
    if draft is None:
        return controller.settings()
    try:
        return AudioSettings.model_validate(draft)
    except ValidationError as error:
        raise CommandError(f"the draft does not validate: {error.errors()[0]['msg']}") from None


def held_to_release(
    controller: AudioController, settings: AudioSettings, macros: list[MacroConfig]
) -> list[str]:
    """Held levels on main-mix inputs' sends that no macro holds in its current state.

    A macro holds the points its last-run steps closed: its `on_steps`'
    `store_levels` while active, its `off_steps`' while not. Anything else
    held on a seat's microphone or another main-mix input is left over (an
    old macro, an old patch) and keeps that send deaf to its fader.
    """
    patch = patch_of(settings)
    main_inputs = {channel for _seat, channel, _bus in patch.seats} | set(patch.others)
    held_by_macros = set()
    for macro in macros:
        steps = macro.on_steps if is_active(macro, controller.view) else macro.off_steps
        for step in steps:
            if isinstance(step, StoreLevelsStep):
                held_by_macros |= {f"{point.channel}:{point.bus}" for point in step.points}
    return sorted(
        point
        for point in controller.stored.all()
        if point.split(":")[0] in main_inputs and point not in held_by_macros
    )


def _release_held(controller: AudioController, settings: AudioSettings, source: str) -> list[str]:
    """Forget the held levels `held_to_release` names; linked sends go back to their fader."""
    with controller.lock:
        released = held_to_release(controller, settings, settings.macros)
        for point in released:
            controller.stored.forget(point)
        controller.publisher.publish("stored_levels", controller.stored.all())
        for point in released:
            channel, bus = point.split(":")
            if bus != "main" and controller.links.state(channel, bus) == "linked":
                controller.set_link(channel, bus, "linked", source)
    return released


def _restore_links(controller: AudioController, expected: Any, source: str) -> dict[str, Any]:
    settings = controller.settings()
    with controller.lock:
        current = controller.links.matrix()
        if expected != current:
            raise CommandError(
                "the link matrix changed since you pressed Restore defaults; press it again"
            )
        changes = link_changes(current, default_links(settings))
        for change in changes:
            controller.set_link(change["channel"], change["bus"], change["after"], source)
        released = _release_held(controller, settings, source)
        return {"links": controller.links.matrix(), "changes": changes, "released": released}


def _note_macros_restored(controller: AudioController, version: Any, source: str) -> dict[str, Any]:
    if not isinstance(version, int):
        raise CommandError("restore_defaults needs the version the restore was saved as")
    settings = controller.settings()
    wanted = default_macros(settings)
    saved = {macro.id: macro for macro in settings.macros}
    missing = [macro.id for macro in wanted if saved.get(macro.id) != macro]
    if missing:
        raise CommandError(
            "the saved audio setup does not carry the default macros "
            f"({', '.join(missing)}); restore and save again"
        )
    released = _release_held(controller, settings, source)
    return {"version": version, "macros": [macro.id for macro in wanted], "released": released}


def _canonical(macro: dict[str, Any]) -> dict[str, Any] | None:
    """`macro` as the settings would store it, or None when it does not validate."""
    try:
        return MacroConfig.model_validate(macro).model_dump(mode="json")
    except ValueError:
        return None
