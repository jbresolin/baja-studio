"""Tests for macro planning and "active" (audio/macros.py).

A `FakeView` stands in for the mixer: plain dictionaries of levels, mutes,
stored levels and links. Each test sets up a mixer, plans steps, and checks
the plan: what would be written, stored or forgotten, in order.
"""

from dataclasses import dataclass, field

import pytest

from baja_studio.adapters.xr18_levels import db_to_level, offset_level
from baja_studio.audio.config import MacroConfig, SetLinkStep
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
    offsets: dict[tuple[str, str], float] = field(default_factory=dict)
    lr_assigns: dict[str, bool] = field(default_factory=dict)

    def lr_assign(self, channel: str) -> bool | None:
        return self.lr_assigns.get(channel)

    def level(self, channel: str, bus: str) -> float | None:
        return self.levels.get((channel, bus))

    def muted(self, channel: str) -> bool | None:
        return self.mutes.get(channel)

    def stored(self, point: str) -> float | None:
        return self.stored_levels.get(point)

    def link_state(self, channel: str, bus: str) -> str:
        return self.links.get((channel, bus), "unlinked")

    def offset_db(self, channel: str, bus: str) -> float:
        return self.offsets.get((channel, bus), 0.0)


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


def test_opening_a_linked_send_on_a_seat_bus_adds_the_seats_offset() -> None:
    # Host 1 keeps PROD 1 2 dB louder than main in their in-ears (an offset,
    # WP9b). Reopening the talkback route goes to main + 2 dB, not to main.
    view = FakeView(
        levels={("5", "main"): 0.75, ("5", "bus1"): 0.0},
        stored_levels={"5:bus1": 0.6},
        links={("5", "bus1"): "linked"},
        offsets={("5", "bus1"): 2.0},
    )

    reopened = plan_open(view, "5", "bus1")[-1]

    assert reopened.value == pytest.approx(db_to_level(2.0))


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


# --- set_link and set_lr_assign (WP9b, "talk to production") ------------------------


def lr(channel: str, on: bool) -> dict[str, object]:
    return {"op": "set_lr_assign", "channel": channel, "on": on}


def link(channel: str, bus: str, state: str) -> dict[str, object]:
    return {"op": "set_link", "channel": channel, "bus": bus, "state": state}


TALK_TO_PRODUCTION = macro(
    on_steps=[lr("1", False), link("1", "bus2", "paused"), close("1", "bus2")],
    off_steps=[lr("1", True), open_("1", "bus2"), link("1", "bus2", "linked")],
)


def test_taking_a_strip_off_lr_changes_only_its_lr_assign() -> None:
    view = FakeView(levels={("1", "main"): 0.75}, lr_assigns={"1": True})

    assert plan_steps([TALK_TO_PRODUCTION.on_steps[0]], view) == [
        PlannedChange("lr_assign", "1", value=False, previous=True)
    ]


def test_pausing_before_closing_leaves_the_send_closed_and_its_level_remembered() -> None:
    # HOST 1 in host 2's in-ears at 0.8; pause the link, then close the send.
    view = FakeView(levels={("1", "bus2"): 0.8}, links={("1", "bus2"): "linked"})

    changes = plan_steps(TALK_TO_PRODUCTION.on_steps[1:], view)

    assert changes == [
        PlannedChange("link", "1", "bus2", "paused", "linked"),
        PlannedChange("store", "1", "bus2", 0.8, None),
        PlannedChange("level", "1", "bus2", 0.0, 0.8),
    ]


def test_restoring_then_relinking_ends_at_the_fader_plus_the_offset() -> None:
    # Released: the send opens at its stored level (its link is still paused,
    # so not the fader), then relinking brings it to the fader plus host 2's
    # offset, as the link matrix does.
    view = FakeView(
        levels={("1", "main"): 0.5, ("1", "bus2"): 0.0},
        links={("1", "bus2"): "paused"},
        stored_levels={"1:bus2": 0.8},
        offsets={("1", "bus2"): 2.0},
    )

    changes = plan_steps(TALK_TO_PRODUCTION.off_steps[1:], view)

    assert [change.kind for change in changes] == ["forget", "level", "link", "level"]
    assert changes[1].value == 0.8
    assert changes[-1].value == pytest.approx(offset_level(0.5, 2.0))


def test_linking_a_send_a_macro_closed_leaves_it_closed() -> None:
    # Restore first, then link: a held send is not reopened by linking.
    view = FakeView(levels={("1", "main"): 0.5}, stored_levels={"1:bus2": 0.8})

    changes = plan_steps([SetLinkStep(channel="1", bus="bus2", state="linked")], view)

    assert changes == [PlannedChange("link", "1", "bus2", "linked", "unlinked")]


def test_a_macro_setting_lr_assigns_is_active_exactly_when_they_are_as_it_says() -> None:
    # The other seats' sends being open or closed does not matter: taking the
    # host off air is what the macro is for (a desk change counts too).
    off_air = FakeView(lr_assigns={"1": False}, levels={("1", "bus2"): 0.8})
    on_air = FakeView(lr_assigns={"1": True}, levels={("1", "bus2"): 0.0})
    unknown = FakeView()

    assert is_active(TALK_TO_PRODUCTION, off_air) is True
    assert is_active(TALK_TO_PRODUCTION, on_air) is False
    assert is_active(TALK_TO_PRODUCTION, unknown) is False
