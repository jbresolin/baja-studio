"""Tests for the real clock (core/clock.py).

These are the only tests that touch real time, and only with zero-length
waits or already-set events, so they still finish instantly.
"""

import threading
from datetime import UTC

from baja_studio.core.clock import SystemClock


def test_wait_returns_true_at_once_when_the_event_is_already_set() -> None:
    event = threading.Event()
    event.set()

    assert SystemClock().wait(event, 60) is True


def test_wait_returns_false_when_time_runs_out_first() -> None:
    assert SystemClock().wait(threading.Event(), 0) is False


def test_call_later_runs_the_callback() -> None:
    called = threading.Event()

    SystemClock().call_later(0, called.set)

    assert called.wait(10)


def test_now_is_in_utc_and_monotonic_never_goes_backwards() -> None:
    clock = SystemClock()

    assert clock.now_utc().tzinfo == UTC
    first = clock.monotonic()
    assert clock.monotonic() >= first
