"""The hosts module's commands: argument checking, then a call into the desk or the settings.

Production: `send_message {to, text, kind?, expires_s?}`, `dismiss {message_id}`,
`push_card {card_id | null}`, `set_card_layout {cards: [{id, visible}]}`,
`set_seat_name {seat_id, screen_name}`, `set_seat {seat_id, color?, enabled?,
audio_channel?}`, `set_texts {canned_messages?, quick_replies?,
message_expires_s?, chime?}`, `clear_messages`, `export_messages`.

Hosts screen: `react {message_id, seat_id, reaction}`, `reply {message_id,
seat_id, text}`, `dismiss`, `mark_shown {message_id, seat_ids}` (delivery) and
`register_screen {seats}` (every 2 s). The last two are kept out of the
command log (`QUIET_COMMANDS`): they repeat on their own and say nothing an
operator did. Everything else is logged with its source by the registry.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from baja_studio.core.module import CommandError
from baja_studio.core.settings import SettingsStore
from baja_studio.core.settings_import import readable_errors
from baja_studio.modules.hosts.config import (
    MAX_MESSAGE_LENGTH,
    REACTIONS,
    SEAT_IDS,
    HostsSettings,
)
from baja_studio.modules.hosts.desk import HostsDesk
from baja_studio.modules.hosts.messages import MESSAGE_KINDS, MessageError

QUIET_COMMANDS = frozenset({"register_screen", "mark_shown"})


class HostsCommands:
    """Runs one command; everything refused becomes a `CommandError` worded for the operator."""

    def __init__(self, desk: HostsDesk, settings: SettingsStore, module_name: str) -> None:
        """`settings` is the shared store; seat and layout commands save through it."""
        self._desk = desk
        self._settings = settings
        self._module = module_name
        self._handlers: dict[str, Callable[[dict[str, Any], str], Any]] = {
            "send_message": self._send_message,
            "react": self._react,
            "reply": self._reply,
            "dismiss": lambda args, source: self._desk.dismiss(_int_arg(args, "message_id")),
            "mark_shown": self._mark_shown,
            "register_screen": self._register_screen,
            "push_card": self._push_card,
            "set_card_layout": self._set_card_layout,
            "set_seat_name": self._set_seat_name,
            "set_seat": self._set_seat,
            "set_texts": self._set_texts,
            "clear_messages": lambda args, source: {"cleared": self._desk.clear()},
            "export_messages": lambda args, source: {"messages": self._desk.store.all()},
        }

    def run(self, command: str, args: dict[str, Any], source: str) -> Any:
        """Carry out `command` for `source`."""
        handler = self._handlers.get(command)
        if handler is None:
            raise CommandError(f"hosts has no command {command!r}")
        try:
            return handler(args, source)
        except MessageError as error:
            raise CommandError(str(error)) from error

    def _current(self) -> HostsSettings:
        values = self._settings.get(self._module)
        assert isinstance(values, HostsSettings)
        return values

    # --- messages --------------------------------------------------------------------

    def _send_message(self, args: dict[str, Any], source: str) -> Any:
        settings = self._current()
        target = args.get("to")
        if target != "all":
            if target not in SEAT_IDS or isinstance(target, bool):
                raise CommandError("'to' must be a seat (1-4) or 'all'")
            seat = settings.seat(target)
            if seat is None or not seat.enabled:
                raise CommandError("that seat is switched off; turn it on under Seats first")
            target = seat.id
        text = _text_arg(args, "text", MAX_MESSAGE_LENGTH)
        kind = args.get("kind", "info")
        if kind not in MESSAGE_KINDS:
            raise CommandError("'kind' must be info, question or urgent")
        expires_s = args.get("expires_s", settings.message_expires_s)
        if isinstance(expires_s, bool) or not isinstance(expires_s, int) or expires_s < 0:
            raise CommandError("'expires_s' must be a whole number of seconds (0: never)")
        return self._desk.send(source, target, text, kind, expires_s or None)

    def _react(self, args: dict[str, Any], source: str) -> Any:
        reaction = args.get("reaction")
        allowed = [*REACTIONS, *self._current().quick_replies]
        if reaction not in allowed:
            raise CommandError(f"'reaction' must be one of: {', '.join(allowed)}")
        return self._desk.react(_int_arg(args, "message_id"), _seat_arg(args, "seat_id"), reaction)

    def _reply(self, args: dict[str, Any], source: str) -> Any:
        text = _text_arg(args, "text", MAX_MESSAGE_LENGTH)
        return self._desk.reply(_int_arg(args, "message_id"), _seat_arg(args, "seat_id"), text)

    def _mark_shown(self, args: dict[str, Any], source: str) -> Any:
        seat_ids = _seat_list_arg(args, "seat_ids")
        if not seat_ids:
            raise CommandError("'seat_ids' must name at least one seat")
        return self._desk.mark_shown(_int_arg(args, "message_id"), seat_ids)

    # --- screens and cards -----------------------------------------------------------

    def _register_screen(self, args: dict[str, Any], source: str) -> Any:
        self._desk.register_screen(source, _seat_list_arg(args, "seats"))
        return {"ok": True}

    def _push_card(self, args: dict[str, Any], source: str) -> Any:
        card_id = args.get("card_id")
        known = {card["id"] for card in self._desk.layout()}
        if card_id is not None and (not isinstance(card_id, str) or card_id not in known):
            raise CommandError("there is no such card; its module may be switched off")
        return {"pushed_card": self._desk.push_card(card_id, source)}

    def _set_card_layout(self, args: dict[str, Any], source: str) -> Any:
        cards = args.get("cards")
        if not isinstance(cards, list):
            raise CommandError("'cards' must be a list of {id, visible}")
        self._save({"layout": cards})
        return {"layout": self._desk.layout()}

    # --- seats and texts (settings) --------------------------------------------------

    def _set_seat_name(self, args: dict[str, Any], source: str) -> Any:
        name = _text_arg(args, "screen_name", 24)
        return self._change_seat(_seat_arg(args, "seat_id"), {"screen_name": name})

    def _set_seat(self, args: dict[str, Any], source: str) -> Any:
        changes: dict[str, Any] = {
            key: args[key] for key in ("color", "enabled", "audio_channel") if key in args
        }
        if not changes:
            raise CommandError("nothing to change: give color, enabled or audio_channel")
        return self._change_seat(_seat_arg(args, "seat_id"), changes)

    def _change_seat(self, seat_id: int, changes: dict[str, Any]) -> Any:
        seats = [seat.model_dump() for seat in self._current().seats]
        for seat in seats:
            if seat["id"] == seat_id:
                seat.update(changes)
        self._save({"seats": seats})
        changed = self._current().seat(seat_id)
        return changed.model_dump() if changed else None

    def _set_texts(self, args: dict[str, Any], source: str) -> Any:
        keys = ("canned_messages", "quick_replies", "message_expires_s", "chime")
        changes = {key: args[key] for key in keys if key in args}
        if not changes:
            raise CommandError(f"nothing to change: give one of {', '.join(keys)}")
        self._save(changes)
        return {key: getattr(self._current(), key) for key in keys}

    def _save(self, patch: dict[str, Any]) -> None:
        try:
            self._settings.update(self._module, patch)
        except ValidationError as invalid:
            raise CommandError(readable_errors(invalid)) from invalid


def _int_arg(args: dict[str, Any], name: str) -> int:
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError(f"'{name}' must be a whole number")
    return value


def _seat_arg(args: dict[str, Any], name: str) -> int:
    seat_id = _int_arg(args, name)
    if seat_id not in SEAT_IDS:
        raise CommandError(f"'{name}' must be a seat: 1, 2, 3 or 4")
    return seat_id


def _seat_list_arg(args: dict[str, Any], name: str) -> list[int]:
    values = args.get(name)
    if not isinstance(values, list):
        raise CommandError(f"'{name}' must be a list of seats (1-4)")
    return [_seat_arg({name: value}, name) for value in values]


def _text_arg(args: dict[str, Any], name: str, limit: int) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(f"'{name}' must be some text")
    text = value.strip()
    if len(text) > limit:
        raise CommandError(f"'{name}' is longer than {limit} characters ({len(text)})")
    return text
