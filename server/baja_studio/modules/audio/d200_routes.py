"""The Ulanzi D200 HTTP contract, served at the service root (`/action/...`).

The 2026 tool's paths and response bodies, unchanged, so the existing
Ulanzi profile only needs its host and port changed. Every path answers GET
and POST. The D200 reads `muted` (mute buttons) or `active` (macro buttons)
from the JSON and compares it with "true".

    /action/mute/:ch[/:state]          {"ok":true,"channel":"1","muted":true}
                                       state "on" mutes, "off" unmutes, else toggles
    /action/nudge/:ch/:delta           {"ok":true,"channel":"1","fader":0.7,"db":"-1.2 dB"}
    /action/nudge-aux/:ch/:bus/:delta  {"ok":true,"channel":"1","bus":"bus4","fader":…,"db":…}
    /action/fader/:ch/:value           {"ok":true}
    /action/scene/:index               {"ok":true}
    /action/macro/:name                {"ok":true,"macro":"…","active":true}  (404 if unknown)
    /action/macro-state/:name          {"macro":"…","active":false}
    /action/state/:ch                  {"channel":"1","muted":false,"fader":0.75,"db":"0.0 dB"}

Errors are `{"error": "…"}` with status 400, worded as in 2026. Bodies are
written the way Express's `res.json` writes them (compact, keys in the
same order, `0` not `0.0`), so the recorded 2026 responses compare byte
for byte (`tests/modules/audio/d200_recorded.json`). One deliberate change:
`db` uses the XR18's real fader law (`adapters/xr18_levels.py`), so mid-travel
values read about 3 dB lower than 2026's approximation did.

Actions go through the command dispatcher as `device:d200`, so they are in
the command log; state polls (once a second per button) are not commands.
"""

import json
import math
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from baja_studio.adapters.xr18_addresses import normalise_channel
from baja_studio.adapters.xr18_levels import format_db
from baja_studio.core.api import services_of
from baja_studio.core.commandlog import device_source
from baja_studio.core.module import CommandError
from baja_studio.core.registry import UnknownModuleError

D200_SOURCE = device_source("d200")
# Worded with an en dash, exactly as the 2026 tool answered.
BAD_BUS_MESSAGE = "bus must be bus1\u2013bus6"
MODULE = "audio"

StateReader = Callable[[], Any]


def js_number(value: float | int) -> str:
    """A number as JavaScript's `JSON.stringify` writes it: 1 not 1.0, 0.00002 not 2e-05.

    Python's `repr` and JavaScript both give the shortest digits that read
    back as the same double; only where the decimal point goes differs
    (ECMAScript Number::toString).
    """
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "null"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    parts = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in parts.digits)
    assert isinstance(parts.exponent, int)
    count, point = len(digits), len(digits) + parts.exponent
    if count <= point <= 21:
        text = digits + "0" * (point - count)
    elif 0 < point <= 21:
        text = f"{digits[:point]}.{digits[point:]}"
    elif -6 < point <= 0:
        text = "0." + "0" * -point + digits
    else:
        exponent = point - 1
        mantissa = digits[0] + (f".{digits[1:]}" if count > 1 else "")
        text = f"{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"
    return sign + text


def express_json(value: Any) -> str:
    """`value` serialised like Express's `res.json` (compact, insertion order, UTF-8)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{express_json(str(key))}:{express_json(item)}" for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list | tuple):
        return "[" + ",".join(express_json(item) for item in value) + "]"
    raise TypeError(f"cannot serialise {type(value).__name__}")


def respond(body: dict[str, Any], status: int = 200) -> Response:
    """A response with Express's body and content type."""
    return Response(
        express_json(body).encode("utf-8"),
        status_code=status,
        media_type="application/json; charset=utf-8",
    )


def parse_float(text: str) -> float | None:
    """JavaScript's `parseFloat`: the leading number in `text`, or None for NaN."""
    match = re.match(r"\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text)
    return float(match.group(0)) if match else None


def parse_int(text: str) -> int | None:
    """JavaScript's `parseInt(text)` (base 10), or None for NaN."""
    match = re.match(r"\s*[+-]?\d+", text)
    return int(match.group(0)) if match else None


def d200_routes() -> APIRouter:
    """The router the audio module mounts at the root."""
    router = APIRouter()

    def command(request: Request, name: str, args: dict[str, Any]) -> Any:
        services = services_of(request)
        _note_d200_seen(request)
        try:
            return services.registry.dispatch_command(MODULE, name, args, D200_SOURCE)
        except UnknownModuleError:
            raise CommandError("the audio module is not installed") from None

    def actions_off(request: Request) -> Response | None:
        settings = services_of(request).settings.get(MODULE)
        if getattr(getattr(settings, "d200", None), "enabled", True):
            return None
        return respond({"error": "D200 actions are turned off in the audio setup"}, 503)

    def channel_or_error(text: str) -> str | Response:
        try:
            return normalise_channel(text)
        except ValueError as error:
            return respond({"error": str(error)}, 400)

    def mute(request: Request, channel: str, state: str | None = None) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        target = channel_or_error(channel)
        if isinstance(target, Response):
            return target
        muted = True if state == "on" else False if state == "off" else None
        try:
            result = command(request, "set_mute", {"channel": target, "muted": muted})
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        return respond({"ok": True, "channel": channel, "muted": result["muted"]})

    def nudge(request: Request, channel: str, delta: str) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        delta_db = parse_float(delta)
        if delta_db is None:
            return respond({"error": "delta must be a number"}, 400)
        target = channel_or_error(channel)
        if isinstance(target, Response):
            return target
        try:
            result = command(request, "nudge", {"channel": target, "delta_db": delta_db})
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        level = result["level"]
        return respond({"ok": True, "channel": channel, "fader": level, "db": format_db(level)})

    def nudge_aux(request: Request, channel: str, bus: str, delta: str) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        delta_db = parse_float(delta)
        if delta_db is None:
            return respond({"error": "delta must be a number"}, 400)
        bus_number = parse_int(bus.replace("bus", "", 1))
        if bus_number is None or not 1 <= bus_number <= 6:
            return respond({"error": BAD_BUS_MESSAGE}, 400)
        target = channel_or_error(channel)
        if isinstance(target, Response):
            return target
        args = {"channel": target, "bus": f"bus{bus_number}", "delta_db": delta_db}
        try:
            result = command(request, "nudge", args)
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        level = result["level"]
        return respond(
            {"ok": True, "channel": channel, "bus": bus, "fader": level, "db": format_db(level)}
        )

    def fader(request: Request, channel: str, value: str) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        level = parse_float(value)
        if level is None:
            return respond({"error": "value must be 0.0-1.0"}, 400)
        target = channel_or_error(channel)
        if isinstance(target, Response):
            return target
        try:
            command(request, "set_fader", {"channel": target, "level": level})
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        return respond({"ok": True})

    def scene(request: Request, index: str) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        number = parse_int(index)
        if number is None or not 1 <= number <= 64:
            return respond({"error": "index 1-64"}, 400)
        try:
            command(request, "recall_scene", {"index": number})
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        return respond({"ok": True})

    def macro(request: Request, name: str) -> Response:
        refused = actions_off(request)
        if refused is not None:
            return refused
        settings = services_of(request).settings.get(MODULE)
        if getattr(settings, "macro", lambda _: None)(name) is None:
            return respond({"ok": False, "error": "not found"}, 404)
        try:
            result = command(request, "run_macro", {"macro": name})
        except CommandError as error:
            return respond({"error": str(error)}, 400)
        return respond({"ok": True, "macro": name, "active": result["active"]})

    def macro_state(request: Request, name: str) -> Response:
        _note_d200_seen(request)
        macros = services_of(request).state.get(MODULE, "macros") or {}
        active = bool((macros.get(name) or {}).get("active", False))
        return respond({"macro": name, "active": active})

    def state(request: Request, channel: str) -> Response:
        _note_d200_seen(request)
        target = channel_or_error(channel)
        if isinstance(target, Response):
            return target
        view = services_of(request).state.get(MODULE, f"channels.{target}") or {}
        level = view.get("fader")
        level = 0 if level is None else level
        return respond(
            {
                "channel": channel,
                "muted": view.get("muted") is True,
                "fader": level,
                "db": format_db(level),
            }
        )

    for path, handler in (
        ("/action/mute/{channel}", mute),
        ("/action/mute/{channel}/{state}", mute),
        ("/action/nudge/{channel}/{delta}", nudge),
        ("/action/nudge-aux/{channel}/{bus}/{delta}", nudge_aux),
        ("/action/fader/{channel}/{value}", fader),
        ("/action/scene/{index}", scene),
        ("/action/macro/{name}", macro),
        ("/action/macro-state/{name}", macro_state),
        ("/action/state/{channel}", state),
    ):
        router.add_api_route(path, handler, methods=["GET", "POST"], include_in_schema=False)
    return router


def _note_d200_seen(request: Request) -> None:
    """Publish when a D200 last called, at most every few seconds (it polls every second)."""
    module = services_of(request).registry.get(MODULE)
    note = getattr(module, "note_d200_request", None)
    if note is not None:
        note()
