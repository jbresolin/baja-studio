"""Tests for the MIDI child (childprocs/midi_child.py) with the fake device (fakes/fake_midi.py).

The first tests call `MidiChild` directly, collecting the events it would
write to the service. The last ones start the real child process with the
fake backend (`BAJA_STUDIO_MIDI_BACKEND=fake`) and talk to it through the
studio's `ChildProcessHost`, as the audio module does; real Python child
processes are allowed in tests (they need no hardware).
"""

import threading
from typing import Any

import pytest

from baja_studio.childprocs.midi_child import (
    BACKEND_ENVIRONMENT_VARIABLE,
    MidiChild,
    UnavailableBackend,
)
from baja_studio.core.childproc import ChildProcessHost, ChildRequestError
from baja_studio.core.clock import SystemClock
from baja_studio.fakes.fake_midi import FakeMidiBackend, bus_select, fader, knob, press, release
from tests.conftest import eventually


class EventLog:
    """Stands in for the child's output: keeps the events it sends."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._lock = threading.Lock()

    def send_event(self, event: str, data: Any = None) -> None:
        with self._lock:
            self.events.append((event, data))

    def write(self, message: dict[str, Any]) -> None:
        pass


def child_with_device() -> tuple[MidiChild, FakeMidiBackend, EventLog]:
    backend = FakeMidiBackend()
    log = EventLog()
    return MidiChild(backend, log), backend, log  # type: ignore[arg-type]


def test_open_finds_the_device_by_part_of_its_name_and_says_so() -> None:
    child, _, log = child_with_device()

    assert child.handle("open", {"match": "smc"}) == {"state": "open", "device": "SMC-Mixer"}
    assert log.events == [("port", {"state": "open", "device": "SMC-Mixer", "message": ""})]


def test_messages_from_the_device_become_midi_events() -> None:
    child, backend, log = child_with_device()
    child.handle("open", {"match": "SMC"})
    log.events.clear()

    backend.inject([press(1, "mute"), fader(2, 1.0)])

    assert log.events == [
        ("midi", {"bytes": [0x90, 0x10, 127]}),
        ("midi", {"bytes": [0xE1, 0x7F, 0x7F]}),
    ]


def test_sending_reaches_the_device() -> None:
    child, backend, _ = child_with_device()
    child.handle("open", {"match": "SMC"})

    assert child.handle("send", {"messages": [[0x90, 0x10, 127], [0x90, 0x11, 0]]}) == 2
    assert backend.sent == [[0x90, 0x10, 127], [0x90, 0x11, 0]]


def test_an_absent_device_is_offline_and_opened_as_soon_as_it_appears() -> None:
    child, backend, log = child_with_device()
    backend.plugged = False

    assert child.handle("open", {"match": "SMC"}) == {"state": "offline", "device": None}

    backend.plugged = True
    child.check_port()  # the watcher thread does this every second

    assert child.status() == {"state": "open", "device": "SMC-Mixer"}
    assert log.events[-1][0] == "port"


def test_unplugging_reports_offline_and_replugging_reopens() -> None:
    child, backend, log = child_with_device()
    child.handle("open", {"match": "SMC"})

    backend.plugged = False
    child.check_port()
    assert log.events[-1] == (
        "port",
        {"state": "offline", "device": None, "message": "SMC-Mixer disconnected"},
    )

    backend.plugged = True
    child.check_port()
    assert log.events[-1] == ("port", {"state": "open", "device": "SMC-Mixer", "message": ""})


def test_sending_to_a_device_that_vanished_fails_and_reports_offline() -> None:
    child, backend, log = child_with_device()
    child.handle("open", {"match": "SMC"})
    backend.plugged = False

    with pytest.raises(OSError):
        child.handle("send", {"messages": [[0x90, 0x10, 127]]})
    assert child.status()["state"] == "offline"
    assert log.events[-1][1]["state"] == "offline"


def test_fake_requests_need_the_fake_backend_and_unknown_methods_are_refused() -> None:
    child, _, _ = child_with_device()

    assert child.handle("fake_unplug", {}) is None
    with pytest.raises(ValueError, match="unknown method"):
        child.handle("explode", {})


def test_without_a_midi_system_the_child_stays_up_and_says_why() -> None:
    # No ALSA sequencer, a broken Windows MIDI driver: the page shows the
    # reason instead of the child crashing and restarting forever.
    child = MidiChild(UnavailableBackend("MIDI is not available: no sequencer"), EventLog())  # type: ignore[arg-type]

    status = child.handle("open", {"match": "SMC"})

    assert status == {
        "state": "offline",
        "device": None,
        "message": "MIDI is not available: no sequencer",
    }


def test_the_script_helpers_build_what_the_m_vave_sends() -> None:
    assert press(3, "solo") == [0x90, 0x0A, 127]
    assert release(3, "solo") == [0x80, 0x0A, 0]
    assert knob(1, -2) == [0xB0, 0x10, 0x42]
    assert bus_select("bus3") == [0x90, 0x60, 127]


@pytest.fixture
def real_child(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real child process, with the fake backend, and the events it sends."""
    monkeypatch.setenv(BACKEND_ENVIRONMENT_VARIABLE, "fake")
    events: list[tuple[str, Any]] = []
    host = ChildProcessHost(
        "baja_studio.childprocs.midi_child",
        SystemClock(),
        "audio",
        on_event=lambda event, data: events.append((event, data)),
    )
    host.start()
    yield host, events
    host.stop()


def test_the_real_child_process_carries_messages_both_ways(real_child: Any) -> None:
    host, events = real_child

    assert host.request("open", {"match": "smc"})["state"] == "open"
    host.request("fake_inject", {"messages": [press(1, "mute")]})
    eventually(lambda: ("midi", {"bytes": [0x90, 0x10, 127]}) in events)

    host.request("send", {"messages": [[0x90, 0x10, 127]]})
    assert host.request("fake_sent") == [[0x90, 0x10, 127]]


def test_the_real_child_reports_the_device_leaving_and_coming_back(real_child: Any) -> None:
    host, events = real_child
    host.request("open", {"match": "smc"})

    host.request("fake_unplug")
    assert host.request("check") == {"state": "offline", "device": None}
    with pytest.raises(ChildRequestError, match="not connected"):
        host.request("send", {"messages": [[0x90, 0x10, 127]]})

    host.request("fake_replug")
    assert host.request("check") == {"state": "open", "device": "SMC-Mixer"}
    eventually(lambda: [e for e in events if e[0] == "port"][-1][1]["state"] == "open")
