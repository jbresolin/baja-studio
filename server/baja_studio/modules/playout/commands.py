"""Playout's commands: argument checking, then a call into the controller.

Every command is logged with its source by the registry. Refusals are
`CommandError`s worded for the operator; OBS being away is one of them.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from baja_studio.adapters.obs_transport import ObsError, ObsNotConnectedError
from baja_studio.core.module import CommandError
from baja_studio.core.settings import SettingsStore
from baja_studio.core.settings_import import readable_errors
from baja_studio.modules.playout.controller import PlayoutController
from baja_studio.modules.playout.return_target import RETURN_MODES
from baja_studio.playout.config import OnAirActions
from baja_studio.playout.library import LibraryError
from baja_studio.playout.playlists import PlaylistError
from baja_studio.playout.queue_store import QueueError
from baja_studio.playout.switcher import SwitchError

Handler = Callable[[PlayoutController, dict[str, Any], str], Any]


def run_command(
    controller: PlayoutController,
    settings: SettingsStore,
    module_name: str,
    command: str,
    args: dict[str, Any],
    source: str,
) -> Any:
    """Carry out one playout command, turning every refusal into a `CommandError`."""
    if command == "mark_stream_start":
        return _mark_stream_start(controller, settings, module_name, args)
    handler = HANDLERS.get(command)
    if handler is None:
        raise CommandError(f"playout has no command {command!r}")
    try:
        return handler(controller, args, source)
    except ObsNotConnectedError as error:
        raise CommandError("OBS is not connected; check the OBS status") from error
    except (ObsError, SwitchError, LibraryError, QueueError, PlaylistError) as error:
        raise CommandError(str(error)) from error


def _int_arg(args: dict[str, Any], name: str) -> int:
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError(f"'{name}' must be a whole number")
    return value


def _int_list_arg(args: dict[str, Any], name: str) -> list[int]:
    values = args.get(name)
    if not isinstance(values, list) or not values:
        raise CommandError(f"'{name}' must be a non-empty list")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CommandError(f"'{name}' must list whole numbers")
    return values


def _text_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise CommandError(f"'{name}' must be text")
    return value


def _queue_add(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    clip_ids = _int_list_arg(args, "clip_ids")
    return {"entry_ids": controller.queue_add(clip_ids, source, bool(args.get("at_front")))}


def _queue_remove(controller: PlayoutController, args: dict[str, Any], source: str) -> None:
    with controller.lock:
        controller.queue.remove(_int_list_arg(args, "entry_ids"))
        controller.queue_changed()


def _queue_move(controller: PlayoutController, args: dict[str, Any], source: str) -> None:
    with controller.lock:
        controller.queue.move(_int_arg(args, "entry_id"), _int_arg(args, "to_index"))
        controller.queue_changed()


def _queue_clear(controller: PlayoutController, args: dict[str, Any], source: str) -> None:
    with controller.lock:
        controller.queue.clear()
        controller.queue_changed()


def _set_clip_field(
    controller: PlayoutController, clip_id: int, change: Callable[[], None]
) -> dict[str, Any] | None:
    with controller.lock:
        change()
        controller.publisher.publish_clips([clip_id])
        return controller.library.get(clip_id)


def _set_notes(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    clip_id, notes = _int_arg(args, "clip_id"), _text_arg(args, "notes")
    return _set_clip_field(
        controller, clip_id, lambda: controller.library.set_notes(clip_id, notes)
    )


def _set_label(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    clip_id, label = _int_arg(args, "clip_id"), _text_arg(args, "label")
    return _set_clip_field(
        controller, clip_id, lambda: controller.library.set_label(clip_id, label)
    )


def _mark_played(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    clip_id, played = _int_arg(args, "clip_id"), args.get("played", True)
    if not isinstance(played, bool):
        raise CommandError("'played' must be true or false")
    return _set_clip_field(
        controller, clip_id, lambda: controller.library.set_played(clip_id, played)
    )


def _set_auto_play_next(controller: PlayoutController, args: dict[str, Any], source: str) -> None:
    enabled = args.get("enabled")
    if not isinstance(enabled, bool):
        raise CommandError("'enabled' must be true or false")
    controller.set_auto_play_next(enabled)


def _mark_stream_start(
    controller: PlayoutController, settings: SettingsStore, module_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Save "now" (or `at`, an ISO time, or null to clear) as the stream-start marker."""
    marker: str | None
    if "at" in args:
        marker = args["at"] if args["at"] is None else _text_arg(args, "at")
    else:
        marker = controller.now_iso()
    try:
        settings.update(module_name, {"export": {"stream_start_marker": marker}})
    except ValueError as error:
        raise CommandError(f"'at' must be an ISO date and time: {error}") from error
    return {"stream_start_marker": marker}


def _set_return_target(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    mode = args.get("mode")
    if mode not in RETURN_MODES:
        raise CommandError("'mode' must be 'previous' or 'chosen'")
    scene = args.get("scene")
    if scene is not None and not isinstance(scene, str):
        raise CommandError("'scene' must be text")
    return controller.set_return_target(mode, scene)


def _playlist_clip_ids(controller: PlayoutController, args: dict[str, Any]) -> list[int]:
    """`clip_ids` (a multi-selection) or `group` (every present clip of a library group)."""
    if "group" in args:
        group = _text_arg(args, "group")
        clip_ids = [
            clip["id"]
            for clip in controller.library.all()
            if clip["group"] == group and not clip["missing"]
        ]
        if not clip_ids:
            raise CommandError(f"the library group {group!r} has no clips")
        return clip_ids
    return _int_list_arg(args, "clip_ids")


def _playlist_create(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    with controller.lock:
        clip_ids = _playlist_clip_ids(controller, args)
        name = args.get("name", args.get("group"))
        if not isinstance(name, str):
            raise CommandError("'name' must be text")
        playlist_id = controller.playlists.create(name, clip_ids)
        controller.playlists_changed()
        return controller.playlists.get(playlist_id)


def _playlist_update(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    playlist_id = _int_arg(args, "playlist_id")
    shuffle = args.get("shuffle")
    if shuffle is not None and not isinstance(shuffle, bool):
        raise CommandError("'shuffle' must be true or false")
    on_air: OnAirActions | None = None
    if "on_air" in args:
        try:
            on_air = None if args["on_air"] is None else OnAirActions.model_validate(args["on_air"])
        except ValidationError as error:
            raise CommandError(f"'on_air': {readable_errors(error)}") from error
    with controller.lock:
        controller.playlists.update(
            playlist_id,
            name=_text_arg(args, "name") if "name" in args else None,
            clip_ids=_int_list_arg(args, "clip_ids") if "clip_ids" in args else None,
            shuffle=shuffle,
            set_on_air="on_air" in args,
            on_air=on_air,
        )
        controller.playlists_changed()
        return controller.playlists.get(playlist_id)


def _playlist_delete(controller: PlayoutController, args: dict[str, Any], source: str) -> None:
    with controller.lock:
        controller.playlists.delete(_int_arg(args, "playlist_id"))
        controller.playlists_changed()


def _playlist_queue(controller: PlayoutController, args: dict[str, Any], source: str) -> Any:
    return {"entry_ids": controller.queue_playlist(_int_arg(args, "playlist_id"), source)}


HANDLERS: dict[str, Handler] = {
    "set_return_target": _set_return_target,
    "playlist_create": _playlist_create,
    "playlist_update": _playlist_update,
    "playlist_delete": _playlist_delete,
    "playlist_queue": _playlist_queue,
    "queue_add": _queue_add,
    "queue_remove": _queue_remove,
    "queue_move": _queue_move,
    "queue_clear": _queue_clear,
    "cue_next": lambda controller, args, source: controller.cue_next(),
    "take": lambda controller, args, source: controller.take(
        source, bool(args.get("cue_if_needed"))
    ),
    "skip": lambda controller, args, source: controller.skip(),
    "drop_next": lambda controller, args, source: controller.drop_next(),
    "return_to_scene": lambda controller, args, source: controller.return_to_scene(),
    "replay_last": lambda controller, args, source: controller.replay_last(source),
    "set_notes": _set_notes,
    "mark_played": _mark_played,
    "set_label": _set_label,
    "set_auto_play_next": _set_auto_play_next,
    "export_aired": lambda controller, args, source: controller.export_aired(),
}
