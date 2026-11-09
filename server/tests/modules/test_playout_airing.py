"""Tests for what happens around an airing (modules/playout/airing.py): on-air actions.

Here the service runs playout *and* audio, with audio on the fake XR18, so
an on-air action really runs an audio macro through the normal command path.
The macro is the seed's "Prod 1 Go Live" (`macro-1774582227201`), which
toggles channel 5's main fader between open and closed; the tests read its
`active` flag from audio's state, which is computed from the mixer.
"""

import threading
from collections.abc import Callable
from typing import Any

import pytest

from baja_studio.core.childproc import EventCallback
from baja_studio.core.module import Module, ModuleContext
from baja_studio.fakes.fake_midi import FakeMidiBackend
from baja_studio.fakes.fake_xr18_in_process import InProcessXr18Network
from baja_studio.fakes.simulated_xr18 import SimulatedXr18
from baja_studio.modules.audio import AudioModule
from baja_studio.modules.audio.midi_worker import MidiChildHost
from baja_studio.playout.on_air import ON_AIR_SOURCE
from tests.conftest import FakeClock, step_fake_time, step_fake_time_until
from tests.modules.audio_studio import MIXER_HOST, MIXER_PORT, InProcessMidiHost
from tests.modules.playout_studio import CLIP_SECONDS, PlayoutStudio, ready_studio

MACRO = "macro-1774582227201"


def run_macro(macro: str = MACRO) -> dict[str, Any]:
    """An on-air action running an audio macro."""
    return {"module": "audio", "command": "run_macro", "args": {"macro": macro}}


@pytest.fixture
def xr18_network(fake_clock: FakeClock) -> InProcessXr18Network:
    return InProcessXr18Network(fake_clock.monotonic)


@pytest.fixture
def playout_extra_modules(xr18_network: InProcessXr18Network) -> list[Module]:
    """Audio beside playout, on the fake XR18, with an in-process M-VAVE child."""
    xr18_network.add_mixer(MIXER_HOST, MIXER_PORT, SimulatedXr18())
    backend = FakeMidiBackend()
    lock = threading.Lock()
    hosts: list[InProcessMidiHost] = []

    def midi_child(context: ModuleContext, on_event: EventCallback) -> MidiChildHost:
        host = InProcessMidiHost(backend, on_event)
        with lock:
            hosts.append(host)
        return host

    return [AudioModule(mixer_transport_factory=xr18_network.open, midi_child_factory=midi_child)]


def soon(playout: PlayoutStudio, condition: Callable[[], bool], within: float = 6.0) -> None:
    """Let fake time pass until `condition()` holds, with both modules' workers asleep."""
    workers = playout_and_audio_workers()
    step_fake_time_until(playout.clock, condition, within, workers)


def playout_and_audio_workers() -> Callable[[], set[int]]:
    def threads() -> set[int]:
        return {
            thread.ident
            for thread in threading.enumerate()
            if thread.ident is not None and thread.name.startswith(("playout.", "audio."))
        }

    return threads


def audio_state(playout: PlayoutStudio, path: str) -> Any:
    return playout.services.state.get("audio", path)


def macro_active(playout: PlayoutStudio) -> bool:
    return bool((audio_state(playout, f"macros.{MACRO}") or {}).get("active"))


def connect_audio(playout: PlayoutStudio) -> None:
    playout.services.settings.update("audio", {"mixer": {"host": MIXER_HOST, "port": MIXER_PORT}})
    soon(playout, lambda: audio_state(playout, "mixer.connection") == "ok")
    # As AudioStudio.connect_mixer: let the initial read finish, so no late
    # answer changes the macro's state in the middle of the test.
    audio = playout.services.registry.get("audio")
    assert isinstance(audio, AudioModule)

    def initial_read_done() -> bool:
        link = audio.controller._link
        return link is not None and link.pending_counts() == (0, 0)

    soon(playout, initial_read_done)
    step_fake_time(playout.clock, 0.1, playout_and_audio_workers())


def audio_log(playout: PlayoutStudio) -> list[tuple[str, str, str]]:
    return [
        (entry["source"], entry["command"], entry["result"])
        for entry in playout.services.command_log.tail("audio", 50)
    ]


def test_a_take_runs_the_start_macro_and_the_end_runs_the_end_macro(
    playout: PlayoutStudio,
) -> None:
    # Bench step: an on-air macro mutes a channel. Start and end both run the
    # toggling macro, so it flips at the take and flips back at the end.
    first, _, _ = ready_studio(playout)
    connect_audio(playout)
    playout.configure(on_air_actions={"on_clip_start": [run_macro()], "on_clip_end": [run_macro()]})
    before = macro_active(playout)
    playout.command("queue_add", clip_ids=[first])

    playout.command("take", cue_if_needed=True)
    soon(playout, lambda: macro_active(playout) is not before)

    soon(playout, lambda: playout.state("program") is None, within=CLIP_SECONDS + 3)
    soon(playout, lambda: macro_active(playout) is before)

    # Both went through audio's command log, marked as playout's on-air actions.
    sent = [entry for entry in audio_log(playout) if entry[0] == ON_AIR_SOURCE]
    assert sent == [(ON_AIR_SOURCE, "run_macro", "ok")] * 2


def test_a_playlist_override_wins_over_the_settings(playout: PlayoutStudio) -> None:
    first, second, _ = ready_studio(playout)
    connect_audio(playout)
    # The settings would run a macro that does not exist; the playlist's own
    # list (empty at the start) replaces them for its clips.
    playout.configure(on_air_actions={"on_clip_start": [run_macro("ghost")]})
    playlist = playout.command("playlist_create", name="Sponsors", clip_ids=[first])
    playout.command(
        "playlist_update",
        playlist_id=playlist["id"],
        on_air={"on_clip_start": [run_macro()], "on_clip_end": []},
    )
    before = macro_active(playout)
    playout.command("playlist_queue", playlist_id=playlist["id"])

    playout.command("take", cue_if_needed=True)

    soon(playout, lambda: macro_active(playout) is not before)
    ghost_failed = ("run_macro", "error: no macro 'ghost'")
    assert all(entry[1:] != ghost_failed for entry in audio_log(playout))

    # A clip queued by hand (not from the playlist) gets the settings' actions.
    playout.command("queue_add", clip_ids=[second])
    playout.command("take", cue_if_needed=True)
    soon(playout, lambda: playout.state("notice") is not None)
    assert "ghost" in playout.state("notice")["message"]


def test_audio_offline_never_stops_the_take_and_the_page_is_told(
    playout: PlayoutStudio,
) -> None:
    # Audio is running but its mixer was never configured: the macro cannot run.
    first, _, _ = ready_studio(playout)
    playout.services.settings.update("audio", {"enabled": False})
    playout.configure(on_air_actions={"on_clip_start": [run_macro()]})
    playout.command("queue_add", clip_ids=[first])

    result = playout.command("take", cue_if_needed=True)

    # The clip is on air regardless...
    assert result["status"] == "on_air"
    assert playout.obs.program_scene == "Playback A"
    # ...and the failure is logged and shown as a notice naming the action.
    soon(playout, lambda: playout.state("notice") is not None)
    message = playout.state("notice")["message"]
    assert message.startswith("on-air action audio.run_macro failed: module 'audio' is stopped")
    assert "at the start of 'a'" in message
