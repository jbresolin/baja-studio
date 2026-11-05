"""Tests for audio's commands (modules/audio/commands.py): arguments, saving, dry runs.

Through the running module (`audio` fixture), the way the pages send them.
"""

from typing import Any

import pytest

from baja_studio.core.module import CommandError
from tests.modules.audio_studio import AudioStudio


def test_levels_can_be_given_in_db_or_as_a_level(audio: AudioStudio) -> None:
    audio.connect_mixer()

    assert audio.command("set_fader", channel="1", db=-10)["level"] == 0.5
    assert audio.command("set_aux", channel="17", bus="bus2", level=0.25) == {
        "channel": "aux",
        "bus": "bus2",
        "level": 0.25,
        "db": "-30.0 dB",
    }


@pytest.mark.parametrize(
    ("command", "args", "message"),
    [
        ("set_fader", {"channel": "40", "level": 0.5}, "Unknown channel: 40"),
        ("set_fader", {"channel": "1"}, "give a level"),
        ("set_aux", {"channel": "lr", "bus": "bus1", "level": 0.5}, "no sends"),
        ("set_aux", {"channel": "1", "bus": "main", "level": 0.5}, "bus must be one of"),
        ("nudge", {"channel": "1"}, "delta_db must be a number"),
        ("set_link", {"channel": "1", "bus": "bus1", "state": "on"}, "linked, paused or unlinked"),
        ("select_bus", {"bus": "bus8"}, "bus must be"),
        ("fly", {}, "no command 'fly'"),
    ],
)
def test_bad_arguments_are_refused_with_a_reason(
    audio: AudioStudio, command: str, args: dict[str, Any], message: str
) -> None:
    audio.connect_mixer()

    with pytest.raises(CommandError, match=message):
        audio.command(command, **args)


def test_save_mappings_saves_the_version_it_was_given(audio: AudioStudio) -> None:
    version = audio.services.settings.record("audio").version

    result = audio.command("save_mappings", version=version, values={"d200": {"enabled": False}})

    assert result == {"version": version + 1}
    assert audio.state("settings_version") == version + 1


def test_save_mappings_on_a_stale_version_is_a_conflict_not_an_overwrite(
    audio: AudioStudio,
) -> None:
    # Two setup pages opened at version N; the first saves; the second must
    # be told, not silently undo the first one's change.
    version = audio.services.settings.record("audio").version
    audio.command("save_mappings", version=version, values={"d200": {"enabled": False}})

    with pytest.raises(CommandError, match="someone else saved"):
        audio.command("save_mappings", version=version, values={"d200": {"enabled": True}})
    assert audio.module.settings().d200.enabled is False


def test_save_mappings_reports_what_is_invalid(audio: AudioStudio) -> None:
    version = audio.services.settings.record("audio").version

    with pytest.raises(CommandError, match=r"not saved: .*unique"):
        audio.command(
            "save_mappings", version=version, values={"macros": [{"id": "a"}, {"id": "a"}]}
        )


def test_a_dry_run_says_what_a_macro_would_change_without_changing_it(audio: AudioStudio) -> None:
    audio.connect_mixer()

    changes = audio.command("dry_run", macro="host1-talk-prod")["changes"]

    assert [change["text"] for change in changes] == [
        "HOST 1: remember 0.0 dB",
        "HOST 1: 0.0 dB → -inf",
    ]
    assert audio.state("channels.1")["fader"] == 0.75


def test_a_dry_run_of_unsaved_steps_from_the_editor(audio: AudioStudio) -> None:
    audio.connect_mixer()
    steps = [
        {"op": "set_aux", "channel": "5", "bus": "bus6", "level": 0.75},
        {"op": "recall_scene", "snapshot": 2},
    ]

    texts = [change["text"] for change in audio.command("dry_run", steps=steps)["changes"]]

    assert texts[0].startswith("PROD 1 → CAMERA OP: ")
    assert texts[1] == "load mixer snapshot 2 (replaces the whole mix)"
    with pytest.raises(CommandError, match="invalid steps"):
        audio.command("dry_run", steps=[{"op": "explode"}])


def test_a_dragged_faders_in_between_values_stay_out_of_the_log(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.command("set_fader", channel="1", level=0.6, dragging=True)
    audio.command("set_fader", channel="1", level=0.61, dragging=True)
    audio.command("set_fader", channel="1", level=0.62)

    assert [entry for entry in audio.log() if entry[1] == "set_fader"] == [
        ("web:alice", "set_fader", "ok")
    ]


def test_stored_levels_can_be_cleared_by_hand(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.command("toggle_send", channel="1", bus="main")

    assert audio.command("clear_stored", point="1:main") == {"point": "1:main", "level": 0.75}
    assert audio.state("stored_levels") == {}


def test_request_sends_reads_one_bus_for_the_visible_and_mapped_strips(audio: AudioStudio) -> None:
    audio.connect_mixer()

    queried = audio.command("request_sends", bus="bus3")["queried"]
    audio.pass_time(0.2)

    # Visible: 1-6, 9, aux; mapped on the M-VAVE: the same ones.
    assert queried == 8
    assert "/ch/12/mix/03/level" not in audio.mixer.queries_received()


def test_simulate_midi_reaches_the_fake_device(audio: AudioStudio) -> None:
    audio.connect_mixer()

    assert audio.command("simulate_midi", messages=[[0x90, 0x10, 127]]) == {"injected": 1}
    audio.soon(lambda: audio.state("channels.1")["muted"] is True)


def test_list_midi_ports_asks_the_child(audio: AudioStudio) -> None:
    assert audio.command("list_midi_ports") == {"inputs": ["SMC-Mixer"], "outputs": ["SMC-Mixer"]}
