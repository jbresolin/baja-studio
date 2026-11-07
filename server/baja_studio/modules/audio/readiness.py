"""The audio module's readiness checks: the XR18, the M-VAVE, the D200 endpoint, the links.

The XR18 is not sent anything: the mixer worker reads `/xinfo` when it
connects and keeps the link alive within its packet budget, so its published
connection already says whether the mixer answers. The D200 self-test builds
the `/action/state/1` body as the route does and checks its shape.
"""

from datetime import datetime
from typing import Any

from baja_studio.audio.config import AudioSettings
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.core.state import StateStore
from baja_studio.modules.audio.d200_routes import channel_state_body

AUDIO_SETUP_LINK = "/audio/config"
D200_STATE_KEYS = {"channel", "muted", "fader", "db"}


def audio_checks(
    settings: AudioSettings,
    audio_state: dict[str, Any],
    state: StateStore,
    links: dict[str, dict[str, str]],
) -> list[ReadinessCheck]:
    """The four checks; `audio_state` is the module's subtree, read when the run starts."""
    host = settings.mixer.host or "its address"
    return [
        ReadinessCheck(
            "mixer",
            "XR18 answers",
            lambda: _mixer(settings, audio_state.get("mixer") or {}),
            fix=(
                f"Check the XR18 is on and its network cable is in; the studio expects it at "
                f"{host} (Audio setup → Devices)."
            ),
            link=AUDIO_SETUP_LINK,
        ),
        ReadinessCheck(
            "mvave",
            "M-VAVE control surface",
            lambda: _mvave(settings, audio_state.get("midi") or {}),
            fix=(
                "Plug the M-VAVE in by USB (or leave it out on purpose); its name must contain "
                f"{settings.midi.device_name_match!r} (Audio setup → M-VAVE)."
            ),
            link=AUDIO_SETUP_LINK,
        ),
        ReadinessCheck(
            "d200",
            "D200 endpoint answers",
            lambda: _d200(settings, audio_state.get("d200") or {}, state),
            fix="This is a bug in the studio: send a diagnostics bundle (home page).",
        ),
        ReadinessCheck(
            "links",
            "No links on hidden channels",
            lambda: _links(settings, links),
            fix=(
                "Show those channels (Audio setup → Channels) or unlink them in the link "
                "matrix, so no hidden fader moves a send."
            ),
            link=AUDIO_SETUP_LINK,
        ),
    ]


def _mixer(settings: AudioSettings, mixer: dict[str, Any]) -> CheckOutcome:
    connection = mixer.get("connection")
    if connection == "ok":
        about = " ".join(str(mixer[key]) for key in ("name", "model", "firmware") if mixer.get(key))
        return passed(f"{about or 'XR18'} at {mixer.get('host') or settings.mixer.host}")
    if connection == "disabled" or not settings.mixer.enabled:
        return warning("the studio is told not to talk to the mixer (Audio setup → Devices)")
    if connection == "not_configured":
        return failed(
            "no mixer address is set", "Enter the XR18's address in Audio setup → Devices."
        )
    return failed(f"{connection}: {mixer.get('message') or 'no answer'}")


def _mvave(settings: AudioSettings, midi: dict[str, Any]) -> CheckOutcome:
    if not settings.midi.enabled:
        return passed("M-VAVE control is off")
    status = midi.get("status")
    if status == "ok":
        return passed(f"open: {midi.get('device')}")
    if status == "offline":
        # It may be unplugged on purpose: the event can run from the page and the D200.
        return warning(midi.get("message") or "the M-VAVE is not plugged in")
    return failed(
        "the MIDI helper process is not answering",
        "Restart the Baja Studio service (Windows Services app); if it keeps failing, "
        "send a diagnostics bundle.",
    )


def _d200(settings: AudioSettings, d200: dict[str, Any], state: StateStore) -> CheckOutcome:
    if not settings.d200.enabled:
        return passed("D200 actions are off (Audio setup → Scenes and D200)")
    body = channel_state_body(state, "1", "1")
    if not _d200_shape_ok(body):
        return failed(f"/action/state/1 has the wrong shape: {body}")
    seen = d200.get("last_seen")
    when = _short_time(seen) if seen else "not since the studio started"
    return passed(f"/action/state/1 answers as the profile expects; last D200 call: {when}")


def _d200_shape_ok(body: dict[str, Any]) -> bool:
    """What the Ulanzi profile reads: `muted` compared with true, a numeric `fader`."""
    fader = body.get("fader")
    return (
        set(body) == D200_STATE_KEYS
        and isinstance(body["channel"], str)
        and isinstance(body["muted"], bool)
        and isinstance(fader, int | float)
        and not isinstance(fader, bool)
        and isinstance(body["db"], str)
    )


def _links(settings: AudioSettings, links: dict[str, dict[str, str]]) -> CheckOutcome:
    hidden = sorted(
        (channel for channel in links if not _visible(settings, channel)), key=_channel_order
    )
    if hidden:
        return warning(f"links on hidden channels: {', '.join(hidden)}")
    return passed(f"{sum(len(buses) for buses in links.values())} links, all on visible channels")


def _visible(settings: AudioSettings, channel: str) -> bool:
    config = settings.channels.get(channel)
    return config is not None and config.visible


def _channel_order(channel: str) -> tuple[int, str]:
    return (int(channel), "") if channel.isdigit() else (99, channel)


def _short_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M:%S UTC")
    except ValueError:
        return str(value)
