"""The audio module's readiness checks: the XR18, the M-VAVE, the D200, taps, LR, the links.

Two checks read the desk's own configuration, as last reported by the mixer
(read on connect, kept fresh by its pushes), and never change it:

- the tap of every linked send: the studio copies the fader into a linked
  send, so one taken after the fader is cut by the fader twice;
- the LR assign of every mapped seat microphone, which "talk to production"
  switches (ADR 0013): a mixer that never reports it may not obey it either.

The XR18 is not sent anything: the mixer worker reads `/xinfo` when it
connects and keeps the link alive within its packet budget, so its published
connection already says whether the mixer answers. The D200 self-test builds
the `/action/state/1` body as the route does and checks its shape.
"""

from datetime import datetime
from typing import Any

from baja_studio.adapters.xr18_addresses import POST_FADER_TAPS
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
    linked_taps: dict[tuple[str, str], int | None],
    seat_lr_assigns: dict[str, bool | None],
) -> list[ReadinessCheck]:
    """The six checks; `audio_state` is the module's subtree, read when the run starts.

    `linked_taps` is the tap of every linked (channel, bus) and
    `seat_lr_assigns` the LR assign of every mapped seat microphone, as last
    read from the mixer (None: never reported).
    """
    mixer = audio_state.get("mixer") or {}
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
            "linked_taps",
            "Linked sends are pre-fader",
            lambda: _linked_taps(settings, mixer, linked_taps),
            fix=(
                "On the XR18 (or in Mixing Station), take these sends before the fader "
                "(pre-fader tap): the studio already copies the fader into a linked send, so "
                "after the fader it is cut twice. The studio only reads this setting."
            ),
            link=AUDIO_SETUP_LINK,
        ),
        ReadinessCheck(
            "lr_assign",
            "Hosts can be taken off air",
            lambda: _lr_assigns(settings, mixer, seat_lr_assigns),
            fix=(
                "'Talk to production' takes a host off the main mix with the channel's LR "
                "assign. Check the mixer's firmware answers it (docs/bench/wp9b.md); until "
                "then do not rely on that macro."
            ),
            link=AUDIO_SETUP_LINK,
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


def _linked_taps(
    settings: AudioSettings, mixer: dict[str, Any], taps: dict[tuple[str, str], int | None]
) -> CheckOutcome:
    """Warn for every linked send taken after the fader, or never reported."""
    if mixer.get("connection") != "ok":
        return warning("not checked: the XR18 is not answering")
    if not taps:
        return passed("no linked sends")

    def name(pair: tuple[str, str]) -> str:
        return f"{settings.label(pair[0])} → {settings.label(pair[1])}"

    after = [name(pair) for pair, tap in sorted(taps.items()) if tap in POST_FADER_TAPS]
    unknown = [name(pair) for pair, tap in sorted(taps.items()) if tap is None]
    if after:
        return warning(f"after the fader, so cut twice: {_some(after)}")
    if unknown:
        return warning(f"the XR18 has not said where these are taken: {_some(unknown)}")
    return passed(f"{len(taps)} linked sends, all before the fader")


def _lr_assigns(
    settings: AudioSettings, mixer: dict[str, Any], lr_assigns: dict[str, bool | None]
) -> CheckOutcome:
    """Warn when a mapped seat microphone's LR assign was never reported."""
    if not lr_assigns:
        return passed("no seat microphone is mapped (Audio setup → Seats)")
    if mixer.get("connection") != "ok":
        return warning("not checked: the XR18 is not answering")
    silent = [settings.label(channel) for channel, on in sorted(lr_assigns.items()) if on is None]
    if silent:
        return warning(f"the XR18 has not reported the LR assign of {', '.join(silent)}")
    return passed(f"LR assign read for {len(lr_assigns)} seat microphones")


def _some(names: list[str], shown: int = 6) -> str:
    """A list for a readiness line: the first few, then how many more."""
    more = len(names) - shown
    return ", ".join(names[:shown]) + (f" and {more} more" if more > 0 else "")


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
