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


# --- WP9b: the seats' audio side ------------------------------------------------------


def seats(*pairs: tuple[str | None, str]) -> list[dict[str, Any]]:
    """Seats 1-4 with the given (channel, in-ear bus) each."""
    return [
        {"seat_id": number, "channel": channel, "iem_bus": bus}
        for number, (channel, bus) in enumerate(pairs, start=1)
    ]


def test_seats_start_on_iem_buses_1_to_4_with_no_microphone_mapped() -> None:
    defaults = AudioSettings()

    assert [(seat.seat_id, seat.channel, seat.iem_bus) for seat in defaults.seats] == [
        (1, None, "bus1"),
        (2, None, "bus2"),
        (3, None, "bus3"),
        (4, None, "bus4"),
    ]
    # Bus 5 is the production loop; PROD 1-4 are channels 5-8 in the 2026 patch.
    assert defaults.production_bus == "bus5"
    assert defaults.production_channels == ["5", "6", "7", "8"]
    assert defaults.hosts_can_adjust_mix is True
    assert defaults.seat_on_bus("bus2") == defaults.seat(2)


def test_two_seats_cannot_share_an_in_ear_bus() -> None:
    # Each seat's offsets live on its own bus; sharing one would mix them up.
    with pytest.raises(ValidationError, match="its own in-ear bus; shared: bus1"):
        settings(seats=seats(("1", "bus1"), ("2", "bus1"), (None, "bus3"), (None, "bus4")))


def test_the_production_bus_cannot_be_a_seats_in_ears() -> None:
    with pytest.raises(ValidationError, match="bus5 is the production bus"):
        settings(seats=seats((None, "bus5"), (None, "bus2"), (None, "bus3"), (None, "bus4")))


def test_seats_must_be_one_to_four_each_once() -> None:
    with pytest.raises(ValidationError, match="exactly the seats 1, 2, 3 and 4"):
        settings(seats=seats((None, "bus1"), (None, "bus2")))


def test_a_production_microphone_listed_twice_is_kept_once_in_order() -> None:
    assert settings(production_channels=["6", "5", "6"]).production_channels == ["6", "5"]
    with pytest.raises(ValidationError, match="has no sends"):
        settings(production_channels=["lr"])


@pytest.mark.parametrize("step", [0.0, -1.0, 6.5])
def test_the_personal_mix_step_is_more_than_0_and_at_most_6_db(step: float) -> None:
    assert settings(personal_mix_step_db=6.0).personal_mix_step_db == 6.0
    with pytest.raises(ValidationError, match="personal_mix_step_db"):
        settings(personal_mix_step_db=step)
