"""Tests for the runnable fake XR18's console (fakes/fake_xr18.py).

The UDP part needs a real socket, so it is tried by hand (see the file's
docstring); the console that drives it is tested with a stand-in.
"""

import io
import threading
from dataclasses import dataclass, field

import pytest

from baja_studio.adapters.osc import OscArgument, OscMessage, encode_message
from baja_studio.fakes.fake_xr18 import parse_value, run_console, shortcut_change
from baja_studio.fakes.simulated_xr18 import SimulatedXr18


@dataclass
class ConsoleTarget:
    """What `run_console` drives: a mixer and a way to make changes."""

    mixer: SimulatedXr18 = field(default_factory=SimulatedXr18)
    changes: list[tuple[str, OscArgument]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def change(self, address: str, value: OscArgument) -> None:
        self.changes.append((address, value))


def test_values_typed_on_the_console_get_their_osc_type() -> None:
    assert parse_value("1") == 1
    assert parse_value("0.5") == 0.5
    assert parse_value("HOST") == "HOST"


def test_console_commands_change_the_mixer(monkeypatch: pytest.MonkeyPatch) -> None:
    target = ConsoleTarget()
    commands = "set /ch/01/mix/fader 0.5\ndrop on\nsnapshot 2\nquit\nset /never 1\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(commands))

    run_console(target)  # type: ignore[arg-type]

    assert target.changes == [("/ch/01/mix/fader", 0.5)]
    assert target.mixer.drop_packets is True
    assert 2 in target.mixer.snapshots


@pytest.mark.parametrize(
    ("line", "change"),
    [
        # Muting writes mix/on 0 (the XR18's inverted sense).
        ("mute 1 on", ("/ch/01/mix/on", 0)),
        ("mute aux off", ("/rtn/aux/mix/on", 1)),
        ("lr 1 off", ("/ch/01/mix/lr", 0)),
        ("fader 9 0.5", ("/ch/09/mix/fader", 0.5)),
        ("send 5 bus1 0.75", ("/ch/05/mix/01/level", 0.75)),
        ("sendon 1 bus5 off", ("/ch/01/mix/05/on", 0)),
        ("tap 1 bus5 pre", ("/ch/01/mix/05/tap", 3)),
        ("tap 1 bus5 post", ("/ch/01/mix/05/tap", 4)),
        # A fresh fake has channel 1 on LR (1): toggling takes it off.
        ("toggle /ch/01/mix/lr", ("/ch/01/mix/lr", 0)),
    ],
)
def test_console_shortcuts_stand_for_the_hosts_card_addresses(
    line: str, change: tuple[str, OscArgument]
) -> None:
    assert shortcut_change(line.split(), SimulatedXr18().values) == change


@pytest.mark.parametrize(
    "line",
    [
        "mute 1 maybe",  # not on/off
        "fader 1 loud",  # not a number
        "send 1 bus9 0.5",  # no such bus
        "lr 40 on",  # no such channel
        "toggle /ch/01/mix/fader",  # not a 0/1 switch
        "set /ch/01/mix/fader 0.5",  # the long form, handled by run_console itself
    ],
)
def test_anything_else_is_not_a_shortcut(line: str) -> None:
    assert shortcut_change(line.split(), SimulatedXr18().values) is None


def test_the_console_applies_a_shortcut_as_a_change_from_the_desk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = ConsoleTarget()
    monkeypatch.setattr("sys.stdin", io.StringIO("lr 1 off\nquit\n"))

    run_console(target)  # type: ignore[arg-type]

    assert target.changes == [("/ch/01/mix/lr", 0)]


def test_a_forgotten_address_is_neither_answered_nor_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rig's lr-assign-silent scenario: a firmware that never reports channel 1's LR assign.
    target = ConsoleTarget()
    monkeypatch.setattr("sys.stdin", io.StringIO("forget /ch/01/mix/lr\nquit\n"))

    run_console(target)  # type: ignore[arg-type]

    # A query for it now gets no answer, and a write to it is ignored.
    query = encode_message(OscMessage("/ch/01/mix/lr", ()))
    write = encode_message(OscMessage("/ch/01/mix/lr", (0,)))
    assert target.mixer.handle("studio", query, 0.0) == []
    assert target.mixer.handle("studio", write, 0.1) == []
    assert "/ch/01/mix/lr" not in target.mixer.values
    # Other addresses still answer.
    fader = encode_message(OscMessage("/ch/01/mix/fader", ()))
    assert len(target.mixer.handle("studio", fader, 0.2)) == 1
