"""Tests for the runnable fake XR18's console (fakes/fake_xr18.py).

The UDP part needs a real socket, so it is tried by hand (see the file's
docstring); the console that drives it is tested with a stand-in.
"""

import io
from dataclasses import dataclass, field

import pytest

from baja_studio.adapters.osc import OscArgument
from baja_studio.fakes.fake_xr18 import parse_value, run_console
from baja_studio.fakes.simulated_xr18 import SimulatedXr18


@dataclass
class ConsoleTarget:
    """What `run_console` drives: a mixer and a way to make changes."""

    mixer: SimulatedXr18 = field(default_factory=SimulatedXr18)
    changes: list[tuple[str, OscArgument]] = field(default_factory=list)

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
