"""Tests for macro planning and "active" (audio/macros.py).

A `FakeView` stands in for the mixer: plain dictionaries of levels, mutes,
stored levels and links. Each test sets up a mixer, plans steps, and checks
the plan: what would be written, stored or forgotten, in order.
"""

from dataclasses import dataclass, field

from baja_studio.audio.config import MacroConfig
from baja_studio.audio.macros import (
    PlannedChange,
    is_active,
    plan_close,
    plan_open,
    plan_steps,
    steps_to_run,
)


@dataclass
class FakeView:
    levels: dict[tuple[str, str], float] = field(default_factory=dict)
    mutes: dict[str, bool] = field(default_factory=dict)
    stored_levels: dict[str, float] = field(default_factory=dict)
    links: dict[tuple[str, str], str] = field(default_factory=dict)

    def level(self, channel: str, bus: str) -> float | None:
        return self.levels.get((channel, bus))

    def muted(self, channel: str) -> bool | None:
        return self.mutes.get(channel)

    def stored(self, point: str) -> float | None:
        return self.stored_levels.get(point)

    def link_state(self, channel: str, bus: str) -> str:
        return self.links.get((channel, bus), "unlinked")


def macro(**fields: object) -> MacroConfig:
    return MacroConfig.model_validate({"id": "m", **fields})


def close(channel: str, *buses: str) -> dict[str, object]:
    return {"op": "store_levels", "points": [{"channel": channel, "bus": bus} for bus in buses]}


def open_(channel: str, *buses: str) -> dict[str, object]:
    return {"op": "restore_levels", "points": [{"channel": channel, "bus": bus} for bus in buses]}


TALK_TO_HOSTS = macro(on_steps=[open_("5", "bus1", "bus2")], off_steps=[close("5", "bus1", "bus2")])


def test_closing_a_route_remembers_its_level_and_silences_it() -> None:
    view = FakeView(levels={("5", "bus1"): 0.6})

    assert plan_close(view, "5", "bus1") == [
        PlannedChange("store", "5", "bus1", 0.6, None),
        PlannedChange("level", "5", "bus1", 0.0, 0.6),
    ]


def test_closing_a_route_that_is_already_silent_keeps_the_level_stored_before() -> None:
    # In 2026 a second press stored "off", and the route could never be reopened.
    view = FakeView(levels={("5", "bus1"): 0.0}, stored_levels={"5:bus1": 0.6})

    assert plan_close(view, "5", "bus1") == []


def test_opening_a_route_brings_back_the_stored_level() -> None:
    view = FakeView(levels={("5", "bus1"): 0.0}, stored_levels={"5:bus1": 0.6})

    assert plan_open(view, "5", "bus1") == [
        PlannedChange("forget", "5", "bus1", None, 0.6),
        PlannedChange("level", "5", "bus1", 0.6, 0.0),
    ]


def test_opening_a_route_with_nothing_stored_goes_to_unity() -> None:
    view = FakeView(levels={("5", "bus1"): 0.0})

    assert plan_open(view, "5", "bus1") == [PlannedChange("level", "5", "bus1", 0.75, 0.0)]


def test_opening_a_linked_send_follows_its_fader_rather_than_the_old_level() -> None:
    # The send is linked to the main fader, which moved while the route was
    # closed: reopen it in step with the fader.
    view = FakeView(
        levels={("5", "main"): 0.5, ("5", "bus1"): 0.0},
        stored_levels={"5:bus1": 0.6},
        links={("5", "bus1"): "linked"},
    )

    assert plan_open(view, "5", "bus1")[-1] == PlannedChange("level", "5", "bus1", 0.5, 0.0)


def test_later_steps_see_what_earlier_steps_planned() -> None:
    # Set the fader, then close it: the stored level is the one just set.
    view = FakeView(levels={("1", "main"): 0.3})
    steps = macro(
        on_steps=[{"op": "set_fader", "channel": "1", "level": 0.7}, close("1", "main")]
    ).on_steps

    assert plan_steps(steps, view) == [
        PlannedChange("level", "1", "main", 0.7, 0.3),
        PlannedChange("store", "1", "main", 0.7, None),
        PlannedChange("level", "1", "main", 0.0, 0.7),
    ]


def test_set_aux_steps_are_marked_to_pause_a_link_and_mutes_and_snapshots_are_planned() -> None:
    steps = macro(
        on_steps=[
            {"op": "set_aux", "channel": "2", "bus": "bus3", "level": 0.5},
            {"op": "set_mute", "channel": "2", "muted": True},
            {"op": "recall_scene", "snapshot": 4},
        ]
    ).on_steps

    plan = plan_steps(steps, FakeView(mutes={"2": False}))

    assert plan == [
        PlannedChange("level", "2", "bus3", 0.5, None, pauses_link=True),
        PlannedChange("mute", "2", value=True, previous=False),
        PlannedChange("snapshot", value=4),
    ]


def test_a_talkback_macro_is_active_exactly_when_its_routes_are_open() -> None:
    closed = FakeView(levels={("5", "bus1"): 0.0, ("5", "bus2"): 0.0})
    open_both = FakeView(levels={("5", "bus1"): 0.6, ("5", "bus2"): 0.7})
    half = FakeView(levels={("5", "bus1"): 0.6, ("5", "bus2"): 0.0})

    assert not is_active(TALK_TO_HOSTS, closed)
    assert is_active(TALK_TO_HOSTS, open_both)
    assert not is_active(TALK_TO_HOSTS, half)


def test_active_follows_the_mixer_even_when_the_change_was_made_elsewhere() -> None:
    # Someone opened the route in Mixing Station: the D200 button lights up.
    view = FakeView(levels={("5", "bus1"): 0.0, ("5", "bus2"): 0.0})
    view.levels[("5", "bus1")] = 0.5
    view.levels[("5", "bus2")] = 0.5

    assert is_active(TALK_TO_HOSTS, view)


def test_unknown_levels_never_make_a_macro_look_active() -> None:
    assert not is_active(TALK_TO_HOSTS, FakeView())


def test_active_compares_set_levels_with_a_small_tolerance() -> None:
    # The mixer quantises levels, so 0.7 may come back as 0.6989.
    exact = macro(on_steps=[{"op": "set_fader", "channel": "9", "level": 0.7}])

    assert is_active(exact, FakeView(levels={("9", "main"): 0.6989}))
    assert not is_active(exact, FakeView(levels={("9", "main"): 0.65}))


def test_mutes_count_and_invisible_steps_do_not() -> None:
    mute_only = macro(on_steps=[{"op": "set_mute", "channel": "3", "muted": True}])
    snapshot_only = macro(on_steps=[{"op": "recall_scene", "snapshot": 2}])

    assert is_active(mute_only, FakeView(mutes={"3": True}))
    assert not is_active(mute_only, FakeView(mutes={"3": False}))
    assert not is_active(snapshot_only, FakeView())


def test_a_trigger_macro_is_never_active_and_always_runs_its_on_steps() -> None:
    trigger = macro(mode="trigger", on_steps=[{"op": "set_mute", "channel": "3", "muted": True}])
    view = FakeView(mutes={"3": True})

    assert not is_active(trigger, view)
    assert steps_to_run(trigger, view) == trigger.on_steps


def test_a_toggle_runs_its_off_steps_when_active_and_on_steps_otherwise() -> None:
    active = FakeView(levels={("5", "bus1"): 0.6, ("5", "bus2"): 0.6})
    inactive = FakeView(levels={("5", "bus1"): 0.0, ("5", "bus2"): 0.0})

    assert steps_to_run(TALK_TO_HOSTS, active) == TALK_TO_HOSTS.off_steps
    assert steps_to_run(TALK_TO_HOSTS, inactive) == TALK_TO_HOSTS.on_steps
