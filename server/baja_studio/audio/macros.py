"""Macros as plans: what a step list would change, and whether a macro is "active".

Pure functions over a `MixerView` (what the mixer is set to, what is stored,
what is linked), so the same code serves running a macro, the editor's
dry-run and the active state on every button (page, M-VAVE LED, D200 image).

**Store and restore.** `store_levels` remembers a send point's level and
silences it ("closes the route"); `restore_levels` opens it again:

- to the main fader's level when the send is *linked* to it (so a reopened
  IEM send is in step with the fader it follows), plus the pair's offset
  (a seat's personal mix, WP9b),
- else to the stored level,
- else to unity (0 dB), as in 2026.

Storing a point that is already silent keeps what was stored before, so a
double press can never store "off" and make the route impossible to reopen
(a 2026 trap).

**Active** is worked out from the mixer, never remembered (in 2026 a
restart or a change in Mixing Station left D200 images wrong): a toggle
macro is active when the mixer already looks the way its `on_steps` would
leave it: each level it sets is there, each route it closes is silent,
each route it opens is open, each mute is as it says. Snapshot recalls,
link changes and `store_levels` without silencing cannot be seen, so they
do not count; a macro with nothing visible is never active. A macro that
sets LR assigns ("talk to production", WP9b) is active exactly when those
LR assigns are as it says: taking a host off air is what it is for.

**Links and LR assigns.** `set_link` pauses or resumes a send's link, as
the link matrix does (resuming brings an open send to its fader plus
offset); `set_lr_assign` takes a strip off the main mix, or puts it back,
without touching its fader.
"""

from dataclasses import dataclass
from typing import Protocol

from baja_studio.adapters.xr18_addresses import MAIN_POINT
from baja_studio.adapters.xr18_levels import UNITY_LEVEL, offset_level
from baja_studio.audio.config import (
    MacroConfig,
    MacroStep,
    RecallSnapshotStep,
    RestoreLevelsStep,
    SetAuxStep,
    SetFaderStep,
    SetLinkStep,
    SetLrAssignStep,
    SetMuteStep,
    StoreLevelsStep,
)

LEVEL_TOLERANCE = 0.01


class MixerView(Protocol):
    """What planning needs to read; the controller provides it under its lock."""

    def level(self, channel: str, bus: str) -> float | None:
        """A send point's level, None if the mixer has not said."""

    def muted(self, channel: str) -> bool | None:
        """Whether a strip is muted, None if unknown."""

    def stored(self, point: str) -> float | None:
        """The level stored for "channel:bus"."""

    def link_state(self, channel: str, bus: str) -> str:
        """ "linked", "paused" or "unlinked"."""

    def offset_db(self, channel: str, bus: str) -> float:
        """How far a linked send sits above or below its main fader (0 dB: none)."""

    def lr_assign(self, channel: str) -> bool | None:
        """Whether a strip is assigned to the main mix (LR), None if unknown."""


@dataclass(frozen=True)
class PlannedChange:
    """One thing a plan does, in order.

    `kind` is "level" (a fader or a send; `bus` is "main" or "busN"),
    "mute", "store" (remember `value` for the point), "forget" (drop the
    stored level), "snapshot" (`value` is its number), "link" (`value` is
    the new link state of channel → bus) or "lr_assign" (`value` is on).
    """

    kind: str
    channel: str = ""
    bus: str = MAIN_POINT
    value: float | bool | int | str | None = None
    previous: float | bool | str | None = None
    # Set on aux writes made directly (set_aux): they pause a linked send.
    pauses_link: bool = False

    @property
    def point(self) -> str:
        """ "channel:bus"."""
        return f"{self.channel}:{self.bus}"


class _Overlay:
    """The view as it will be after the changes planned so far."""

    def __init__(self, view: MixerView) -> None:
        self.view = view
        self.levels: dict[tuple[str, str], float] = {}
        self.mutes: dict[str, bool] = {}
        self.stored: dict[str, float | None] = {}
        self.links: dict[tuple[str, str], str] = {}
        self.lr_assigns: dict[str, bool] = {}

    def level(self, channel: str, bus: str) -> float | None:
        return self.levels.get((channel, bus), self.view.level(channel, bus))

    def stored_level(self, point: str) -> float | None:
        return self.stored[point] if point in self.stored else self.view.stored(point)

    def link_state(self, channel: str, bus: str) -> str:
        return self.links.get((channel, bus), self.view.link_state(channel, bus))


def plan_steps(steps: list[MacroStep], view: MixerView) -> list[PlannedChange]:
    """The changes `steps` would make, in order, each step seeing the ones before it."""
    overlay = _Overlay(view)
    changes: list[PlannedChange] = []
    for step in steps:
        changes += _plan_step(step, overlay)
    return changes


def _plan_step(step: MacroStep, overlay: _Overlay) -> list[PlannedChange]:
    if isinstance(step, SetFaderStep):
        return [_level(overlay, step.channel, MAIN_POINT, step.level)]
    if isinstance(step, SetAuxStep):
        return [_level(overlay, step.channel, step.bus, step.level, pauses_link=True)]
    if isinstance(step, SetMuteStep):
        previous = overlay.mutes.get(step.channel, overlay.view.muted(step.channel))
        overlay.mutes[step.channel] = step.muted
        return [PlannedChange("mute", step.channel, value=step.muted, previous=previous)]
    if isinstance(step, StoreLevelsStep):
        return [
            change
            for point in step.points
            for change in plan_close(overlay, point.channel, point.bus, step.silence)
        ]
    if isinstance(step, RestoreLevelsStep):
        return [
            change
            for point in step.points
            for change in plan_open(overlay, point.channel, point.bus)
        ]
    if isinstance(step, SetLinkStep):
        return _plan_link(overlay, step.channel, step.bus, step.state)
    if isinstance(step, SetLrAssignStep):
        previous = overlay.lr_assigns.get(step.channel, overlay.view.lr_assign(step.channel))
        overlay.lr_assigns[step.channel] = step.on
        return [PlannedChange("lr_assign", step.channel, value=step.on, previous=previous)]
    assert isinstance(step, RecallSnapshotStep)
    return [PlannedChange("snapshot", value=step.snapshot)]


def _level(
    overlay: _Overlay, channel: str, bus: str, level: float, pauses_link: bool = False
) -> PlannedChange:
    previous = overlay.level(channel, bus)
    overlay.levels[(channel, bus)] = level
    return PlannedChange("level", channel, bus, level, previous, pauses_link)


def plan_close(
    view: MixerView | _Overlay, channel: str, bus: str, silence: bool = True
) -> list[PlannedChange]:
    """Store a point's level and (by default) silence it."""
    overlay = view if isinstance(view, _Overlay) else _Overlay(view)
    point = f"{channel}:{bus}"
    current = overlay.level(channel, bus)
    changes = []
    if current is not None and current > 0.0:
        overlay.stored[point] = current
        changes.append(PlannedChange("store", channel, bus, current, overlay.view.stored(point)))
    if silence and current != 0.0:
        changes.append(_level(overlay, channel, bus, 0.0))
    return changes


def plan_open(view: MixerView | _Overlay, channel: str, bus: str) -> list[PlannedChange]:
    """Bring a point back: to its linked main level, its stored level, or unity."""
    overlay = view if isinstance(view, _Overlay) else _Overlay(view)
    point = f"{channel}:{bus}"
    stored = overlay.stored_level(point)
    target = stored if stored is not None else UNITY_LEVEL
    if bus != MAIN_POINT and overlay.link_state(channel, bus) == "linked":
        main = overlay.level(channel, MAIN_POINT)
        if main is not None:
            target = offset_level(main, overlay.view.offset_db(channel, bus))
    changes = []
    if stored is not None:
        overlay.stored[point] = None
        changes.append(PlannedChange("forget", channel, bus, None, stored))
    changes.append(_level(overlay, channel, bus, target))
    return changes


def _plan_link(overlay: _Overlay, channel: str, bus: str, state: str) -> list[PlannedChange]:
    """Change a link; linking brings an open send to its fader plus offset, as the matrix does.

    A send a macro closed (it has a stored level) stays closed: restore it
    first, then link, to have it follow its fader again.
    """
    previous = overlay.link_state(channel, bus)
    overlay.links[(channel, bus)] = state
    changes = [PlannedChange("link", channel, bus, state, previous)]
    main = overlay.level(channel, MAIN_POINT)
    held = overlay.stored_level(f"{channel}:{bus}") is not None
    if state == "linked" and main is not None and not held:
        changes.append(
            _level(overlay, channel, bus, offset_level(main, overlay.view.offset_db(channel, bus)))
        )
    return changes


def is_open(level: float | None) -> bool | None:
    """True for a level above off, False for off, None when unknown."""
    return None if level is None else level > 0.0


def is_active(macro: MacroConfig, view: MixerView) -> bool:
    """Whether the mixer already looks the way `on_steps` would leave it (see module doc)."""
    if macro.mode != "toggle":
        return False
    expectations = _expectations(macro.on_steps)
    # A macro that takes strips off (or on) the main mix is about that: the
    # LR assigns alone decide, whatever else it does around them (ADR 0013).
    lr_only = {key: value for key, value in expectations.items() if key[0] == "lr"}
    if lr_only:
        expectations = lr_only
    if not expectations:
        return False
    for (kind, channel, bus), expected in expectations.items():
        if kind == "mute":
            if view.muted(channel) is not expected:
                return False
            continue
        if kind == "lr":
            if view.lr_assign(channel) is not expected:
                return False
            continue
        level = view.level(channel, bus)
        if level is None:
            return False
        if expected == "open" and not level > 0.0:
            return False
        if expected == "silent" and level != 0.0:
            return False
        if isinstance(expected, float) and abs(level - expected) > LEVEL_TOLERANCE:
            return False
    return True


Expectation = float | bool | str


def _expectations(steps: list[MacroStep]) -> dict[tuple[str, str, str], Expectation]:
    """The visible end state of `steps`, the last step touching a point winning."""
    expected: dict[tuple[str, str, str], Expectation] = {}
    for step in steps:
        if isinstance(step, SetFaderStep):
            expected[("level", step.channel, MAIN_POINT)] = step.level
        elif isinstance(step, SetAuxStep):
            expected[("level", step.channel, step.bus)] = step.level
        elif isinstance(step, SetMuteStep):
            expected[("mute", step.channel, "")] = step.muted
        elif isinstance(step, StoreLevelsStep) and step.silence:
            for point in step.points:
                expected[("level", point.channel, point.bus)] = "silent"
        elif isinstance(step, RestoreLevelsStep):
            for point in step.points:
                expected[("level", point.channel, point.bus)] = "open"
        elif isinstance(step, SetLrAssignStep):
            expected[("lr", step.channel, "")] = step.on
    return expected


def steps_to_run(macro: MacroConfig, view: MixerView) -> list[MacroStep]:
    """What pressing the macro does now: `off_steps` if it is an active toggle, else `on_steps`."""
    if macro.mode == "toggle" and is_active(macro, view):
        return macro.off_steps
    return macro.on_steps
