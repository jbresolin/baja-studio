"""Tests for the on-air action runner (playout/on_air.py), on its own.

The runner is handed a stand-in for the registry's `dispatch_command` that
records calls (or fails), so these tests need no other module. The real
command path, with audio and the fake XR18, is in
tests/modules/test_playout_airing.py.
"""

import logging
from typing import Any

import pytest

from baja_studio.playout.config import OnAirAction, OnAirActions
from baja_studio.playout.on_air import ON_AIR_SOURCE, OnAirActionRunner, actions_for

MUTE = OnAirAction(module="audio", command="run_macro", args={"macro": "mute"})
OPEN = OnAirAction(module="audio", command="run_macro", args={"macro": "open"})


class Recorder:
    """A dispatch that records every call, failing those for `failing` macros."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str]] = []
        self.failing = failing or set()

    def __call__(self, module: str, command: str, args: dict[str, Any], source: str) -> Any:
        self.calls.append((module, command, args, source))
        if args.get("macro") in self.failing:
            raise RuntimeError(f"module {module!r} is stopped")
        return {"ok": True}


def runner_with(dispatch: Recorder | None) -> tuple[OnAirActionRunner, list[str]]:
    notices: list[str] = []
    return OnAirActionRunner(dispatch, logging.getLogger("test"), notices.append), notices


def test_queued_actions_are_sent_in_order_marked_as_on_air() -> None:
    dispatch = Recorder()
    runner, notices = runner_with(dispatch)

    runner.queue([MUTE, OPEN], "on_clip_start", "Enduro · carro 42")
    runner.send_pending()

    assert dispatch.calls == [
        ("audio", "run_macro", {"macro": "mute"}, ON_AIR_SOURCE),
        ("audio", "run_macro", {"macro": "open"}, ON_AIR_SOURCE),
    ]
    assert notices == []


def test_a_failure_is_reported_once_and_the_next_action_still_runs() -> None:
    dispatch = Recorder(failing={"mute"})
    runner, notices = runner_with(dispatch)

    runner.queue([MUTE, OPEN], "on_clip_end", "Logo")
    runner.send_pending()

    assert len(dispatch.calls) == 2
    assert notices == [
        "on-air action audio.run_macro failed: module 'audio' is stopped (at the end of 'Logo')"
    ]


def test_queueing_never_sends_anything_by_itself() -> None:
    # The take queues actions under playout's lock; only the worker sends.
    dispatch = Recorder()
    runner, _ = runner_with(dispatch)

    runner.queue([MUTE], "on_clip_start", "x")

    assert dispatch.calls == []


def test_without_a_command_path_every_action_is_reported() -> None:
    runner, notices = runner_with(None)

    runner.queue([MUTE], "on_clip_start", "x")
    runner.send_pending()

    assert notices == [
        "on-air action audio.run_macro not sent: no command path (at the start of 'x')"
    ]


@pytest.mark.parametrize(
    ("override", "expected"),
    [(None, [MUTE]), (OnAirActions(on_clip_start=[OPEN]), [OPEN]), (OnAirActions(), [])],
)
def test_a_playlist_override_replaces_the_settings_actions(
    override: OnAirActions | None, expected: list[OnAirAction]
) -> None:
    # An override with an empty list means "nothing for this playlist", not
    # "fall back to the settings".
    settings = OnAirActions(on_clip_start=[MUTE])

    assert actions_for("on_clip_start", settings, override) == expected
