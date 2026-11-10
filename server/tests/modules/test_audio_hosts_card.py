"""Tests for audio's card on the hosts screen (modules/audio/hosts_card.py).

The card itself only says where its data is: `audio.hosts_view`, drawn by
the frontend's `seat_audio` component (what is in it: test_audio_hosts_view.py).
This file also checks which macros count as talkback: those opening a send
to the `talkback_bus` setting (bus 6 by default), worked out from the
audio settings.
"""

import pytest
from pydantic import ValidationError

from baja_studio.audio.config import AudioSettings, MacroConfig
from baja_studio.modules.audio.hosts_card import audio_hosts_card, talkback_macros


def test_the_card_renders_audios_hosts_view_with_the_seat_audio_component() -> None:
    card = audio_hosts_card()

    assert (card.id, card.state_path, card.component) == (
        "audio",
        "audio.hosts_view",
        "seat_audio",
    )
    assert card.priority == 50


def test_the_2026_macros_that_talk_to_the_camera_op_are_the_talkback_ones() -> None:
    # The three "Prod n → Cam" macros of the 2026 profile (ids kept for the D200).
    assert talkback_macros(AudioSettings().macros, "bus6") == [
        "macro-1774582749617",
        "macro-1774582877605",
        "prod3-talk-cam",
    ]


def test_a_macro_turning_a_send_to_the_bus_up_counts_and_turning_it_down_does_not() -> None:
    macros = [
        MacroConfig.model_validate(
            {
                "id": "up",
                "on_steps": [{"op": "set_aux", "channel": "5", "bus": "bus6", "level": 0.8}],
            }
        ),
        MacroConfig.model_validate(
            {
                "id": "down",
                "on_steps": [{"op": "set_aux", "channel": "5", "bus": "bus6", "level": 0.0}],
            }
        ),
        MacroConfig.model_validate(
            {"id": "mute", "on_steps": [{"op": "set_mute", "channel": "5", "muted": True}]}
        ),
    ]

    assert talkback_macros(macros, "bus6") == ["up"]


def test_another_talkback_bus_picks_the_macros_opening_that_bus() -> None:
    settings = AudioSettings.model_validate(
        {
            "talkback_bus": "bus5",
            "seats": [
                {"seat_id": 1, "iem_bus": "bus1"},
                {"seat_id": 2, "iem_bus": "bus2"},
                {"seat_id": 3, "iem_bus": "bus3"},
                {"seat_id": 4, "iem_bus": "bus4"},
            ],
            "production_bus": "bus6",
            "macros": [
                {
                    "id": "to-bus5",
                    "on_steps": [{"op": "set_aux", "channel": "5", "bus": "bus5", "level": 0.8}],
                },
                {
                    "id": "to-bus6",
                    "on_steps": [{"op": "set_aux", "channel": "5", "bus": "bus6", "level": 0.8}],
                },
            ],
        }
    )

    assert talkback_macros(settings.macros, settings.talkback_bus) == ["to-bus5"]


def test_the_talkback_bus_defaults_to_bus_6_and_must_be_an_aux_bus() -> None:
    assert AudioSettings().talkback_bus == "bus6"
    with pytest.raises(ValidationError):
        AudioSettings.model_validate({"talkback_bus": "main"})
