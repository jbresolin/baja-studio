"""Tests for audio's card on the hosts screen (modules/audio/hosts_card.py).

The card tells the hosts screen which macros mean "production is talking to
the camera operator" (they open a send to the `talkback_bus` setting, bus 6
by default), worked out from the audio settings; the screen then looks
their ids up in `audio.macros`.
"""

import pytest
from pydantic import ValidationError

from baja_studio.audio.config import AudioSettings, MacroConfig
from baja_studio.modules.audio.hosts_card import audio_hosts_card, talkback_macros


def test_the_2026_macros_that_talk_to_the_camera_op_are_the_talkback_ones() -> None:
    card = audio_hosts_card(AudioSettings())

    # The three "Prod n → Cam" macros of the 2026 profile (ids kept for the D200).
    assert card.options["talkback_macros"] == [
        "macro-1774582749617",
        "macro-1774582877605",
        "prod3-talk-cam",
    ]
    assert card.options["talkback_label"] == "CAMERA OP"
    assert (card.id, card.state_path, card.component) == ("audio", "audio", "audio_state")


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


def test_the_talkback_bus_is_a_setting_so_a_repatched_desk_needs_no_code() -> None:
    settings = AudioSettings.model_validate(
        {
            "talkback_bus": "bus5",
            "channels": {"bus5": {"label": "CAM OP"}},
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

    card = audio_hosts_card(settings)

    assert card.options["talkback_macros"] == ["to-bus5"]
    assert card.options["talkback_label"] == "CAM OP"


def test_an_unlabelled_talkback_bus_is_called_camera_op() -> None:
    settings = AudioSettings.model_validate(
        {"talkback_bus": "bus2", "channels": {"bus2": {"label": ""}}}
    )

    assert audio_hosts_card(settings).options["talkback_label"] == "camera op"


def test_the_talkback_bus_defaults_to_bus_6_and_must_be_an_aux_bus() -> None:
    assert AudioSettings().talkback_bus == "bus6"
    with pytest.raises(ValidationError):
        AudioSettings.model_validate({"talkback_bus": "main"})
