"""Tests for the first-start link matrix and macros (audio/defaults.py).

The link matrix only seeds a fresh install; an existing one keeps its own.
The rules come from the studio's audio topology (LIVESTACK-34) and "talk to
production" (ADR 0013 decision 8).
"""

from baja_studio.audio import defaults
from baja_studio.audio.config import (
    AudioSettings,
    SetLinkStep,
    SetLrAssignStep,
    StoreLevelsStep,
)

SEAT_MICROPHONES = ("1", "2", "3", "4")  # HOST 1-2, GUEST 1-2
PRODUCTION_MICROPHONES = ("5", "6", "7", "8")  # PROD 1-4


def test_every_source_of_the_main_mix_follows_main_into_the_four_in_ear_buses() -> None:
    # The in-ears are "main plus my offsets": the seats, the shotgun and PC
    # AUDIO are linked to buses 1-4.
    for channel in (*SEAT_MICROPHONES, "9", "aux"):
        assert {bus: defaults.LINKS[channel][bus] for bus in defaults.IEM_BUSES} == dict.fromkeys(
            defaults.IEM_BUSES, "linked"
        ), channel


def test_production_microphones_are_never_linked_into_the_in_ears() -> None:
    # Production is not part of the main mix the hosts hear. A production
    # microphone reaches one in-ear bus only when production speaks to that
    # host privately ("Prod N → Hosts", shown as "production → you"); linked,
    # it would follow a fader that is down whenever the producer is off air.
    for channel in PRODUCTION_MICROPHONES:
        linked_into = set(defaults.LINKS.get(channel, {})) & set(defaults.IEM_BUSES)
        assert linked_into == set(), channel


def test_the_seats_microphones_follow_main_into_production_and_the_camera_op() -> None:
    # Bus 5 is main plus the production microphones; bus 6 is the camera op's.
    for channel in SEAT_MICROPHONES:
        assert (defaults.LINKS[channel]["bus5"], defaults.LINKS[channel]["bus6"]) == (
            "linked",
            "linked",
        )


def test_every_seat_has_a_talk_to_production_macro_that_leaves_its_own_in_ears_alone() -> None:
    settings = AudioSettings()
    for macro_id, channel, own_bus in (
        ("host1-talk-prod", "1", "bus1"),
        ("host2-talk-prod", "2", "bus2"),
        ("guest1-talk-prod", "3", "bus3"),
        ("guest2-talk-prod", "4", "bus4"),
    ):
        macro = settings.macro(macro_id)
        assert macro is not None, macro_id
        others = {bus for bus in defaults.IEM_BUSES if bus != own_bus}
        on = macro.on_steps
        # LR assign off, then the other seats' links paused, then their sends closed.
        assert on[0] == SetLrAssignStep(channel=channel, on=False)
        assert {step.bus for step in on if isinstance(step, SetLinkStep)} == others
        [closing] = [step for step in on if isinstance(step, StoreLevelsStep)]
        assert {point.bus for point in closing.points} == others
        assert on.index(closing) > max(
            index for index, step in enumerate(on) if isinstance(step, SetLinkStep)
        )
        # Nothing touches the fader, the seat's own in-ears or buses 5-6.
        touched = {point.bus for point in closing.points}
        assert not touched & {"main", own_bus, "bus5", "bus6"}


def test_the_seed_is_a_valid_matrix_of_inputs_and_aux_buses() -> None:
    for channel, buses in defaults.LINKS.items():
        assert channel in (*(str(number) for number in range(1, 17)), "aux")
        assert set(buses) <= {f"bus{number}" for number in range(1, 7)}
        assert set(buses.values()) <= {"linked", "paused"}
