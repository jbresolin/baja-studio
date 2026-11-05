"""Tests for audio's settings model and its 2026 seeds (audio/config.py, audio/defaults.py).

Saving goes through this model, so a mistake made on the setup page
(a typo'd channel, a button pointing at a deleted macro) is refused with
a message, instead of breaking something at the event.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from baja_studio.adapters.xr18_addresses import CHANNEL_IDS
from baja_studio.audio.config import AudioSettings, SendPoint, is_valid_macro_id, plain_name


def settings(**changes: Any) -> AudioSettings:
    return AudioSettings.model_validate({**AudioSettings().model_dump(), **changes})


def test_the_defaults_are_the_2026_event() -> None:
    defaults = AudioSettings()

    assert defaults.channels["1"].label == "HOST 1"
    assert defaults.channels["aux"].label == "PC AUDIO"  # 2026's channel 17
    assert defaults.channels["bus6"].label == "CAMERA OP"
    assert [c for c, config in defaults.channels.items() if config.visible] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "9",
        "aux",
    ]
    assert defaults.midi.faders["5"] == "9"  # strip 5 was the shotgun
    assert defaults.midi.row_buses == {"solo": "main", "rec": "bus5", "sel": "bus1"}


def test_the_d200_profiles_macro_ids_are_kept_as_they_were() -> None:
    # The Ulanzi profile calls /action/macro/<id>; renaming would break a button.
    ids = {macro.id for macro in AudioSettings().macros}

    assert {
        "host1-talk-prod",
        "host2-talk-prod",
        "prod1-talk-hosts",
        "macro-1774581643177",
        "macro-1774582227201",
        "macro-1774582340101",
        "macro-1774582749617",
        "macro-1774582877605",
    } <= ids


def test_every_channel_is_present_even_if_a_saved_document_lacks_some() -> None:
    loaded = settings(channels={"3": {"label": "GUEST", "color": "#123456", "visible": True}})

    assert list(loaded.channels) == list(CHANNEL_IDS)
    assert loaded.channels["3"].label == "GUEST"
    assert loaded.channels["4"].label == ""


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"channels": {"17": {}}}, "unknown channel"),
        ({"macros": [{"id": "a"}, {"id": "a"}]}, "unique"),
        ({"macros": [{"id": "has space"}]}, "pattern"),
        ({"scenes": [{"index": 1, "macro": "ghost"}]}, "unknown macro"),
        ({"scenes": [{"index": 1}]}, "snapshot, a macro"),
        ({"scenes": [{"index": 1, "snapshot": 1}, {"index": 1, "snapshot": 2}]}, "unique"),
    ],
)
def test_mistakes_are_refused_with_a_reason(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**changes)


def test_a_button_running_a_macro_that_does_not_exist_is_refused() -> None:
    midi = AudioSettings().midi.model_dump()
    midi["buttons"]["1_sel"] = {"type": "macro", "macro": "ghost"}

    with pytest.raises(ValidationError, match="unknown macro 'ghost'"):
        settings(midi=midi)


@pytest.mark.parametrize(
    ("midi_change", "message"),
    [
        ({"faders": {"9": "1"}}, "no strip"),
        ({"buttons": {"1_play": {"type": "mute"}}}, "no button"),
        ({"row_buses": {"solo": "bus9"}}, "row bus"),
        ({"faders": {"1": "ch1"}}, "unknown channel"),
    ],
)
def test_the_mvave_mapping_only_names_real_controls(
    midi_change: dict[str, Any], message: str
) -> None:
    midi = {**AudioSettings().midi.model_dump(), **midi_change}

    with pytest.raises(ValidationError, match=message):
        settings(midi=midi)


def test_a_send_point_on_a_bus_needs_an_input_channel() -> None:
    assert SendPoint(channel="lr", bus="main").key == "lr:main"
    with pytest.raises(ValidationError, match="no send"):
        SendPoint(channel="lr", bus="bus1")


def test_steps_are_told_apart_by_their_op() -> None:
    loaded = settings(
        macros=[
            {
                "id": "x",
                "on_steps": [{"op": "set_aux", "channel": "aux", "bus": "bus2", "level": 0.5}],
            }
        ]
    )

    assert type(loaded.macros[0].on_steps[0]).__name__ == "SetAuxStep"
    with pytest.raises(ValidationError):
        settings(
            macros=[
                {
                    "id": "x",
                    "on_steps": [{"op": "set_aux", "channel": "lr", "bus": "bus2", "level": 0.5}],
                }
            ]
        )


def test_labels_fall_back_to_plain_names() -> None:
    assert AudioSettings().label("1") == "HOST 1"
    assert AudioSettings().label("12") == "Ch 12"
    assert plain_name("bus3") == "Bus 3"
    assert plain_name("lr") == "Main"


def test_macro_ids_must_be_url_safe() -> None:
    assert is_valid_macro_id("macro-1774581643177")
    assert not is_valid_macro_id("a/b")
