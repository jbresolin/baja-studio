"""Tests for the UVC child (childprocs/uvc_child.py) with the fake duvc library.

The first tests call `UvcChild` directly, collecting the events it would
write to the service. The last ones start the real child process with the
fake backend (`BAJA_STUDIO_UVC_BACKEND=fake`) and talk to it through the
studio's `ChildProcessHost`, as the cameras module does.
"""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.childprocs.uvc_child import (
    BACKEND_ENVIRONMENT_VARIABLE,
    UnavailableBackend,
    UvcChild,
)
from baja_studio.childprocs.uvc_duvc import DuvcBackend
from baja_studio.core.childproc import ChildProcessHost, ChildRequestError
from baja_studio.core.clock import SystemClock
from baja_studio.fakes.fake_duvc import (
    PIXY_PATH,
    S600_A_PATH,
    S600_B_PATH,
    FakeDuvcLibrary,
    studio_devices,
)
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


def child_with_devices() -> tuple[UvcChild, FakeDuvcLibrary, EventLog]:
    library = FakeDuvcLibrary(studio_devices())
    log = EventLog()
    return UvcChild(DuvcBackend(library), log), library, log  # type: ignore[arg-type]


def test_both_axes_of_a_two_axis_move_are_written() -> None:
    # The pixy_ptz bug: the second axis of a move was lost. Here pan and
    # tilt go out in one request, and both reach the device, in order.
    child, library, _ = child_with_devices()

    answer = child.handle(
        "write_controls",
        {
            "path": PIXY_PATH,
            "controls": {
                "pan": {"value": 20, "auto": False},
                "tilt": {"value": -10, "auto": False},
            },
        },
    )

    assert answer == {"pan": None, "tilt": None}
    assert library.writes == [(PIXY_PATH, "Pan", 20, "Manual"), (PIXY_PATH, "Tilt", -10, "Manual")]


def test_one_refused_control_does_not_stop_the_next() -> None:
    child, library, _ = child_with_devices()

    answer = child.handle(
        "write_controls",
        {
            "path": PIXY_PATH,
            "controls": {"pan": {"value": 400, "auto": False}, "tilt": {"value": 5, "auto": False}},
        },
    )

    # Pan 400 is beyond ±150: refused with the device's reason; tilt still goes.
    assert answer["pan"] is not None and "out of range" in answer["pan"]
    assert answer["tilt"] is None
    assert (
        child.handle("read_controls", {"path": PIXY_PATH, "names": ["tilt"]})["controls"]["tilt"][
            "value"
        ]
        == 5
    )


def test_read_controls_gives_only_the_asked_controls_the_device_has() -> None:
    child, _, _ = child_with_devices()

    answer = child.handle("read_controls", {"path": S600_A_PATH, "names": ["gain", "pan"]})

    # An S600 has no pan, so only gain comes back.
    assert list(answer["controls"]) == ["gain"]
    assert answer["errors"] == {}


def test_the_device_list_is_announced_only_when_it_changes() -> None:
    child, library, log = child_with_devices()

    first = child.handle("list_devices", {})
    child.check_devices()  # the watcher thread's 2 s look; nothing changed
    assert len(first) == 3
    assert log.events == []

    library.device(S600_B_PATH).plugged = False
    child.check_devices()

    assert log.events[-1][0] == "devices"
    assert [device["path"] for device in log.events[-1][1]["devices"]] == [S600_A_PATH, PIXY_PATH]


def test_without_uvc_the_child_stays_up_and_says_why() -> None:
    # Not Windows, or duvc-ctl missing: no devices, and the reason is shown.
    child = UvcChild(UnavailableBackend(), EventLog(), "not on Windows")  # type: ignore[arg-type]

    assert child.handle("status", {}) == {"available": False, "message": "not on Windows"}
    assert child.handle("list_devices", {}) == []
    with pytest.raises(ValueError, match="fake UVC backend"):
        child.handle("fake_unplug", {"path": S600_A_PATH})


def test_unknown_methods_are_refused() -> None:
    child, _, _ = child_with_devices()

    with pytest.raises(ValueError, match="unknown method"):
        child.handle("explode", {})


@pytest.fixture
def real_child(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ChildProcessHost, list[Any]]]:
    """The real child process, with the fake library, and the events it sends."""
    monkeypatch.setenv(BACKEND_ENVIRONMENT_VARIABLE, "fake")
    events: list[tuple[str, Any]] = []
    host = ChildProcessHost(
        "baja_studio.childprocs.uvc_child",
        SystemClock(),
        "cameras",
        on_event=lambda event, data: events.append((event, data)),
    )
    host.start()
    yield host, events
    host.stop()


def test_the_real_child_process_lists_writes_and_reads(
    real_child: tuple[ChildProcessHost, list[Any]],
) -> None:
    host, _ = real_child

    assert host.request("status") == {"available": True, "message": ""}
    assert len(host.request("list_devices")) == 3
    host.request("write_controls", {"path": PIXY_PATH, "controls": {"zoom": {"value": 120}}})
    zoom = host.request("read_controls", {"path": PIXY_PATH, "names": ["zoom"]})["controls"]["zoom"]

    assert (zoom["value"], zoom["min"], zoom["max"]) == (120, 100, 150)


def test_the_real_child_reports_a_device_leaving(
    real_child: tuple[ChildProcessHost, list[Any]],
) -> None:
    host, events = real_child
    host.request("list_devices")

    host.request("fake_unplug", {"path": S600_A_PATH})
    host.request("check")  # rather than waiting 2 s for the watcher

    eventually(lambda: any(event == "devices" for event, _ in events))
    with pytest.raises(ChildRequestError, match="not plugged in"):
        host.request("read_controls", {"path": S600_A_PATH})
