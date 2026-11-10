"""Audio's commands (WebSocket and `POST /api/commands`); each returns a JSON result.

Levels are 0..1 (`level`) or decibels (`db`), whichever is given.

    set_fader     {channel, level | db}            → {channel, level, db}
    set_aux       {channel, bus, level | db}       → same, plus bus (pauses a linked send)
    nudge         {channel, bus?="main", delta_db} → same
    set_mute      {channel, muted?: bool | null}   → {channel, muted}; null toggles
    set_pan       {channel, pan}                   → {channel, pan}
    toggle_send   {channel, bus}                   → {point, open}
    set_link      {channel, bus, state}            → {links}
    run_macro     {macro}                          → {macro, active}
    dry_run       {macro?, steps?, which?}         → {changes: [...]}; which: next | on | off
    recall_scene  {index}                          → {index}
    select_bus    {bus | null}                     → {selected_bus}
    request_sends {bus}                            → {queried}
    clear_stored  {point}                          → {point, level}
    save_mappings {version, values}                → {version}; a stale version is refused
    midi_learn    {action: "arm" | "cancel"}       → {armed}
    list_midi_ports                                → {inputs, outputs}
    simulate_midi {messages}                       → {injected} (fake MIDI backend only)

From the hosts screen's personal-mix overlay (checked against the seat,
`seat_commands.py`): `set_seat_offset`, `reset_seat_offsets`. From the
settings export/import page (`routing_commands.py`): `export_routing`,
`import_routing`.

A page dragging a fader marks the in-between values `dragging: true`; only
the value it lets go at is kept in the command log (`wants_logged`).
"""

import json
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter, ValidationError

from baja_studio.adapters.xr18_addresses import (
    AUX_BUSES,
    MAIN_POINT,
    SEND_SOURCES,
    normalise_channel,
    normalise_point_bus,
)
from baja_studio.adapters.xr18_levels import db_to_level, format_db
from baja_studio.audio.config import MacroStep
from baja_studio.audio.macros import PlannedChange, steps_to_run
from baja_studio.core.childproc import ChildRequestError
from baja_studio.core.module import CommandError
from baja_studio.core.settings import SettingsConflictError, SettingsStore
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.modules.audio.controller import AudioController
from baja_studio.modules.audio.midi_worker import MidiWorker
from baja_studio.modules.audio.routing_commands import ROUTING_COMMANDS, run_routing_command
from baja_studio.modules.audio.seat_commands import SEAT_COMMANDS, run_seat_command
from baja_studio.modules.audio.surface import MvaveSurface

DRAG_COMMANDS = ("set_fader", "set_aux", "set_pan")
_STEPS = TypeAdapter(list[MacroStep])


def channel_argument(args: dict[str, Any], sends: bool = False) -> str:
    """The `channel` argument as a channel id; `sends` requires an input."""
    try:
        channel = normalise_channel(str(args.get("channel", "")))
    except ValueError as error:
        raise CommandError(str(error)) from None
    if sends and channel not in SEND_SOURCES:
        raise CommandError(f"channel {channel} has no sends; use 1-16 or aux")
    return channel


def bus_argument(args: dict[str, Any], allow_main: bool) -> str:
    """The `bus` argument: "bus1"…"bus6", or also "main" when allowed."""
    try:
        bus = normalise_point_bus(str(args.get("bus", "")))
    except ValueError as error:
        raise CommandError(str(error)) from None
    if bus == MAIN_POINT and not allow_main:
        raise CommandError(f"bus must be one of {', '.join(AUX_BUSES)}")
    return bus


def level_argument(args: dict[str, Any]) -> float:
    """`level` (0..1) or `db`."""
    try:
        if args.get("level") is not None:
            return float(args["level"])
        if args.get("db") is not None:
            return db_to_level(float(args["db"]))
    except (TypeError, ValueError):
        pass
    raise CommandError("give a level (0..1) or db")


def number_argument(args: dict[str, Any], name: str) -> float:
    """A required number."""
    try:
        return float(args[name])
    except (KeyError, TypeError, ValueError):
        raise CommandError(f"{name} must be a number") from None


def describe(change: PlannedChange, label: Callable[[str], str]) -> dict[str, Any]:
    """A planned change as the dry-run shows it."""
    where = f"{label(change.channel)}" + (
        "" if change.bus == MAIN_POINT else f" → {label(change.bus)}"
    )
    if change.kind == "level":
        before = "unknown" if change.previous is None else format_db(float(change.previous))
        text = f"{where}: {before} → {format_db(float(change.value or 0.0))}"
    elif change.kind == "mute":
        text = f"{label(change.channel)}: {'mute' if change.value else 'unmute'}"
    elif change.kind == "store":
        text = f"{where}: remember {format_db(float(change.value or 0.0))}"
    elif change.kind == "forget":
        text = f"{where}: use the remembered level"
    else:
        text = f"load mixer snapshot {change.value} (replaces the whole mix)"
    return {"kind": change.kind, "point": change.point, "value": change.value, "text": text}


def run_command(
    controller: AudioController,
    surface: MvaveSurface,
    midi: MidiWorker,
    settings: SettingsStore,
    module_name: str,
    command: str,
    args: dict[str, Any],
    source: str,
) -> Any:
    """Carry out one audio command; raises `CommandError` with an operator-readable message."""
    if command in SEAT_COMMANDS:
        return run_seat_command(controller, command, args, source)
    if command in ROUTING_COMMANDS:
        return run_routing_command(controller, command, args, source)
    if command in ("set_fader", "set_aux", "nudge"):
        to_send = command == "set_aux" or (
            command == "nudge" and args.get("bus") not in (None, MAIN_POINT)
        )
        if to_send:
            channel, bus = channel_argument(args, sends=True), bus_argument(args, allow_main=False)
        else:
            channel, bus = channel_argument(args), MAIN_POINT
        if command == "nudge":
            level = controller.nudge(channel, bus, number_argument(args, "delta_db"), source)
        else:
            level = controller.set_level(channel, bus, level_argument(args), source)
        return {"channel": channel, "bus": bus, "level": level, "db": format_db(level)}
    if command == "set_mute":
        channel = channel_argument(args)
        muted = args.get("muted")
        return {
            "channel": channel,
            "muted": controller.set_mute(channel, None if muted is None else bool(muted), source),
        }
    if command == "set_pan":
        channel = channel_argument(args, sends=True)
        return {
            "channel": channel,
            "pan": controller.set_pan(channel, number_argument(args, "pan"), source),
        }
    if command == "toggle_send":
        channel = channel_argument(args)
        bus = bus_argument(args, allow_main=True)
        return {"point": f"{channel}:{bus}", "open": controller.toggle_send(channel, bus, source)}
    if command == "set_link":
        channel = channel_argument(args, sends=True)
        controller.set_link(
            channel, bus_argument(args, allow_main=False), str(args.get("state")), source
        )
        return {"links": controller.links.matrix()}
    if command == "run_macro":
        macro_id = str(args.get("macro", ""))
        return {"macro": macro_id, "active": controller.run_macro(macro_id, source)}
    if command == "dry_run":
        return {"changes": _dry_run(controller, args)}
    if command == "recall_scene":
        index = int(number_argument(args, "index"))
        controller.recall_scene(index, source)
        return {"index": index}
    if command == "select_bus":
        selected = None if args.get("bus") is None else bus_argument(args, allow_main=False)
        return {"selected_bus": controller.select_bus(selected, source)}
    if command == "request_sends":
        return {"queried": controller.request_sends(bus_argument(args, allow_main=False))}
    if command == "clear_stored":
        point = str(args.get("point", ""))
        with controller.lock:
            forgotten = controller.stored.forget(point)
            controller.publisher.publish("stored_levels", controller.stored.all())
        return {"point": point, "level": forgotten}
    if command == "save_mappings":
        return _save_mappings(settings, module_name, args)
    if command == "midi_learn":
        if args.get("action", "arm") == "cancel":
            surface.cancel_learn()
            return {"armed": False}
        surface.arm_learn(source)
        return {"armed": True}
    if command in ("list_midi_ports", "simulate_midi"):
        method, params = (
            ("list_ports", {})
            if command == "list_midi_ports"
            else ("fake_inject", {"messages": args.get("messages", [])})
        )
        try:
            result = midi.request(method, params)
        except (DeviceOfflineError, ChildRequestError) as error:
            raise CommandError(str(error)) from None
        return result if command == "list_midi_ports" else {"injected": result}
    raise CommandError(f"audio has no command {command!r}")


def _dry_run(controller: AudioController, args: dict[str, Any]) -> list[dict[str, Any]]:
    """What a macro (or an unsaved step list from the editor) would change now."""
    if args.get("steps") is not None:
        try:
            steps = _STEPS.validate_python(args["steps"])
        except ValidationError as error:
            raise CommandError(f"invalid steps: {error.errors()[0]['msg']}") from None
    else:
        macro = controller.settings().macro(str(args.get("macro", "")))
        if macro is None:
            raise CommandError(f"no macro {args.get('macro')!r}")
        which = args.get("which", "next")
        with controller.lock:
            steps = {"on": macro.on_steps, "off": macro.off_steps}.get(
                which, steps_to_run(macro, controller.view)
            )
    settings = controller.settings()
    return [describe(change, settings.label) for change in controller.dry_run(steps)]


def _save_mappings(
    settings: SettingsStore, module_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    values = args.get("values")
    if not isinstance(values, dict) or not isinstance(args.get("version"), int):
        raise CommandError("save_mappings needs the version you edited and the values")
    try:
        settings.update(module_name, values, expected_version=args["version"])
    except SettingsConflictError as conflict:
        raise CommandError(
            f"someone else saved the audio setup (now version {conflict.current_version}); "
            "load the latest and redo your change"
        ) from None
    except ValidationError as invalid:
        problems = json.loads(invalid.json(include_url=False))
        detail = "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in problems)
        raise CommandError(f"not saved: {detail}") from None
    return {"version": settings.record(module_name).version}
