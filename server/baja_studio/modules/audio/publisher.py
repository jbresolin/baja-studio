"""Audio's state subtree: what every page (and the D200 routes) read.

    audio.mixer        connection ("not_configured" | "connecting" | "ok" |
                       "offline" | "disabled"), host, name/model/firmware from
                       /xinfo, message, packets_per_s {sent, received}
    audio.channels.<id> fader (0..1 or null), db ("-7.5 dB" | "-inf" | null),
                       muted, name (typed on the mixer), pan, sends {busN: level}
    audio.links        {channel: {bus: "linked" | "paused"}}
    audio.stored_levels {"5:bus1": level}
    audio.macros.<id>  {active}
    audio.midi         status ("disabled" | "starting" | "ok" | "offline"),
                       device, message, selected_bus, learn {armed, by, captured}
    audio.last_source.<key>  who last changed "5:main", "5:bus1", "5:mute"...
    audio.d200         last_seen (ISO time of the D200's last request)
    audio.settings_version  bumped on every save, so an open setup page can
                       tell someone else saved without reloading its draft

Channels are published one at a time (`channels.5`), so a fader sweep is
a stream of small patches, not the whole mixer.
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.xr18_addresses import AUX_BUSES, CHANNEL_IDS, SEND_SOURCES
from baja_studio.adapters.xr18_levels import format_db
from baja_studio.audio.mixer_cache import MixerCache

UpdateState = Callable[[str, Any], None]


def initial_audio_state() -> dict[str, Any]:
    """Nothing known: no mixer, no MIDI, every channel unknown."""
    return {
        "mixer": {
            "connection": "not_configured",
            "host": "",
            "message": "",
            "name": None,
            "model": None,
            "firmware": None,
            "packets_per_s": {"sent": 0, "received": 0},
        },
        "channels": {channel: empty_channel(channel) for channel in CHANNEL_IDS},
        "links": {},
        "stored_levels": {},
        "macros": {},
        "midi": {
            "status": "starting",
            "device": None,
            "message": "",
            "selected_bus": None,
            "learn": {"armed": False, "by": None, "captured": None},
        },
        "last_source": {},
        "d200": {"last_seen": None},
        "settings_version": 0,
    }


def empty_channel(channel: str) -> dict[str, Any]:
    """A channel the mixer has not told us about yet."""
    view: dict[str, Any] = {"fader": None, "db": None, "muted": None, "name": None}
    if channel in SEND_SOURCES:
        view["pan"] = None
        view["sends"] = dict.fromkeys(AUX_BUSES)
    return view


def channel_view(cache: MixerCache, channel: str) -> dict[str, Any]:
    """One channel as the pages see it."""
    fader = cache.faders.get(channel)
    view: dict[str, Any] = {
        "fader": fader,
        "db": None if fader is None else format_db(fader),
        "muted": cache.mutes.get(channel),
        "name": cache.names.get(channel),
    }
    if channel in SEND_SOURCES:
        view["pan"] = cache.pans.get(channel)
        view["sends"] = {bus: cache.sends.get((channel, bus)) for bus in AUX_BUSES}
    return view


class AudioPublisher:
    """Writes audio's state, remembering what it last wrote to skip repeats."""

    def __init__(self, update_state: UpdateState) -> None:
        """`update_state` is the module context's `update_state`."""
        self._update = update_state
        self._last: dict[str, Any] = {}
        # Workers and commands publish from their own threads.
        self._lock = threading.RLock()

    def publish(self, path: str, value: Any) -> None:
        """Set `path` unless it already holds exactly `value`."""
        with self._lock:
            if self._last.get(path) == value:
                return
            self._last[path] = value
            self._update(path, value)

    def channel(self, cache: MixerCache, channel: str) -> None:
        """Publish one channel."""
        self.publish(f"channels.{channel}", channel_view(cache, channel))

    def all_channels(self, cache: MixerCache) -> None:
        """Publish every channel (after a reconnect or a cache reset)."""
        for channel in CHANNEL_IDS:
            self.channel(cache, channel)

    def mixer(self, **fields: Any) -> None:
        """Merge `fields` into `audio.mixer`."""
        with self._lock:
            current = dict(self._last.get("mixer") or initial_audio_state()["mixer"])
            current.update(fields)
            self.publish("mixer", current)

    def midi(self, **fields: Any) -> None:
        """Merge `fields` into `audio.midi`."""
        with self._lock:
            current = dict(self._last.get("midi") or initial_audio_state()["midi"])
            current.update(fields)
            self.publish("midi", current)

    def current(self, path: str) -> Any:
        """What was last published at `path` (None if nothing yet)."""
        with self._lock:
            return self._last.get(path)

    def last_source(self, key: str, source: str) -> None:
        """Remember who last touched a control ("5:main", "5:mute"...)."""
        self.publish(f"last_source.{key}", source)
