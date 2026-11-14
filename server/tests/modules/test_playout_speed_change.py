"""Tests for live speed changes one at a time (modules/playout/speed_change.py), and their failures.

The service runs with the playout module and the fake OBS (every clip
10 s at 25 fps; no OBS delays here: the races are in
test_playout_transport_races.py). A live change waits on the fake clock,
so it runs on its own thread while the test moves time to exactly when that
thread wakes (`run_stepping`, the same as `change_speed`).
"""

import threading
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.core.module import CommandError
from tests.conftest import eventually
from tests.modules.playout_studio import PlayoutStudio
from tests.modules.test_playout_transport import (
    LONGEST_STEP_WAIT_SECONDS,
    change_speed,
    on_air,
)


def start_change(playout: PlayoutStudio, speed_percent: int) -> tuple[threading.Thread, list[Any]]:
    """Send `set_program_speed` on its own thread; returns the thread and where its answer goes."""
    answers: list[Any] = []

    def send() -> None:
        try:
            answers.append(playout.command("set_program_speed", speed_percent=speed_percent))
        except CommandError as error:
            answers.append(error)

    thread = threading.Thread(target=send)
    thread.start()
    return thread, answers


def wakes_in(playout: PlayoutStudio, thread: threading.Thread) -> float | None:
    wakes_at = playout.clock.wake_up_time_of(thread.ident)
    return None if wakes_at is None else wakes_at - playout.clock.monotonic()


def wait_until_it_sleeps(playout: PlayoutStudio, thread: threading.Thread) -> None:
    """Until the change sleeps between two steps (not inside a request), or has finished."""

    def between_steps() -> bool:
        seconds = wakes_in(playout, thread)
        return seconds is not None and seconds <= LONGEST_STEP_WAIT_SECONDS

    eventually(lambda: between_steps() or not thread.is_alive())


def run_stepping(playout: PlayoutStudio, thread: threading.Thread) -> None:
    """Move fake time to each of the thread's wake-ups until it has finished."""
    while thread.is_alive():
        wait_until_it_sleeps(playout, thread)
        seconds = wakes_in(playout, thread)
        if thread.is_alive() and seconds is not None:
            playout.clock.advance(max(seconds, 0.0))
    thread.join()


def program_source(playout: PlayoutStudio) -> str:
    return "baja_program_a" if playout.state("program.slot") == "a" else "baja_program_b"


def test_a_click_during_a_change_replaces_the_waiting_one_and_the_last_wish_wins(
    playout: PlayoutStudio,
) -> None:
    # A change to 50 % is under way (waiting for OBS's seek in the other
    # source to land). Meanwhile someone clicks 100 %, then 25 %: each answers at once
    # "queued", and only the last (25 %) is carried out after the first one.
    on_air(playout)
    playout.pass_time(1.0)
    first, answers = start_change(playout, 50)
    wait_until_it_sleeps(playout, first)
    assert playout.state("speed_change") == {"changing": True, "target": 50, "error": None}

    queued = [
        playout.command("set_program_speed", "web:bob", speed_percent=100),
        playout.command("set_program_speed", "web:carol", speed_percent=25),
    ]
    run_stepping(playout, first)

    assert [answer["status"] for answer in queued] == ["queued", "queued"]
    # The first caller carried out both its own change and the last wish.
    assert answers == [{"status": "done", "speed_percent": 25}]
    assert playout.state("program.speed_percent") == 25
    assert playout.obs.inputs[program_source(playout)].settings["speed_percent"] == 25
    # Two swaps (50 then 25), never the 100 % that was replaced.
    speeds = [
        record.data["inputSettings"]["speed_percent"]
        for record in playout.obs.requests_of("SetInputSettings")
        if "speed_percent" in record.data["inputSettings"]
    ]
    assert speeds[-2:] == [50, 25]
    assert 100 not in speeds[1:]
    assert playout.state("speed_change") == {"changing": False, "target": None, "error": None}


def test_a_change_obs_cannot_finish_in_3_s_cuts_nothing_and_says_so(
    playout: PlayoutStudio,
) -> None:
    # The other source never starts the file (OBS hangs opening it): after
    # 3 s the change gives up. Program never left the clip on air, which
    # plays on at 100 %; the error is the command's and stays on the page.
    on_air(playout)
    playout.pass_time(1.0)
    cuts_before = len(playout.obs.timeline.program_cuts)
    playout.obs.stuck_files.add(playout.state("program")["clip"]["file_path"])
    started_at = playout.clock.monotonic()

    with pytest.raises(CommandError, match="speed change failed, still at 100 %"):
        change_speed(playout, 50)

    assert playout.clock.monotonic() - started_at == pytest.approx(3.0, abs=0.05)
    assert len(playout.obs.timeline.program_cuts) == cuts_before
    assert playout.state("program.slot") == "a"
    assert playout.state("program.speed_percent") == 100
    assert playout.obs.media_of("baja_program_a").state == MediaState.PLAYING
    # The half-loaded off-air source is stopped, and program never went black.
    assert playout.obs.media_of("baja_program_b").state == MediaState.STOPPED
    assert playout.obs.timeline.black_on_program == []
    change = playout.state("speed_change")
    assert change["changing"] is False
    assert "did not start" in change["error"]


def test_the_next_change_clears_the_last_error(playout: PlayoutStudio) -> None:
    on_air(playout)
    playout.obs.stuck_files.add(playout.state("program")["clip"]["file_path"])
    with pytest.raises(CommandError):
        change_speed(playout, 50)
    playout.obs.stuck_files.clear()

    change_speed(playout, 50)

    assert playout.state("speed_change")["error"] is None
    assert playout.state("program.speed_percent") == 50


def test_going_back_to_100_too_close_to_the_end_is_refused_and_the_clip_ends_normally(
    playout: PlayoutStudio,
) -> None:
    # Back to 100 % continues from about where the clip is; with less than a
    # second of clip left that is refused: nothing cut or reloaded, and the
    # slow clip on air plays on to its end.
    on_air(playout)
    playout.pass_time(5.0)
    change_speed(playout, 50)
    slot = playout.state("program.slot")
    source = "baja_program_a" if slot == "a" else "baja_program_b"
    playout.command("transport", action="seek", position_s=9.5)

    with pytest.raises(CommandError, match="too close to the end"):
        change_speed(playout, 100)

    assert playout.state("program.speed_percent") == 50
    assert playout.state("program.slot") == slot
    # Half a second of clip at half speed: it ends within a second of air.
    playout.pass_time(1.5)
    playout.soon(lambda: playout.obs.media_of(source).state == MediaState.ENDED)
    # Playout hears of the end from OBS's event a moment later, and logs it.
    playout.pass_time(0.5)
    playout.soon(lambda: playout.command("export_aired")["entries"][0]["ended_at"] is not None)


def test_a_replay_near_the_end_still_jumps_back_and_plays(playout: PlayoutStudio) -> None:
    # Bench: "a clip less than 2 s from its end". A replay goes 3 s back,
    # so half a second before the end is no problem: from 6.5 s, slowly.
    on_air(playout)
    playout.command("transport", action="seek", position_s=9.5)

    change_speed(playout, 50)

    assert playout.state("program.speed_percent") == 50
    assert playout.state("program.slot") == "b"
    cursor = playout.obs.media_of("baja_program_b").cursor(playout.clock.monotonic())
    assert 6.5 <= cursor <= 6.5 + 0.35
    assert playout.obs.timeline.black_on_program == []
