"""Tests for the hosts screen tracker (modules/hosts/screens.py).

A hosts screen says which seats it shows every 2 s; a seat is online while
some screen showing it has been heard from in the last 6 s. The tracker is
told the time on every call, so these tests just pass numbers.
"""

from baja_studio.modules.hosts.screens import SCREEN_TIMEOUT_SECONDS, ScreenTracker


def test_a_registered_screen_puts_its_seats_online() -> None:
    tracker = ScreenTracker()

    changed = tracker.heard_from("web:monitor", [2, 1], now=0.0)

    assert changed is True
    assert tracker.online_seats() == {1: 1, 2: 1}
    assert tracker.screen_count() == 1


def test_repeating_the_same_seats_is_not_a_change() -> None:
    tracker = ScreenTracker()
    tracker.heard_from("web:monitor", [1], now=0.0)

    # The screen's heartbeat two seconds later says the same thing.
    assert tracker.heard_from("web:monitor", [1], now=2.0) is False
    # Picking another seat on that screen is a change.
    assert tracker.heard_from("web:monitor", [1, 2], now=4.0) is True


def test_two_screens_showing_one_seat_are_counted() -> None:
    tracker = ScreenTracker()
    tracker.heard_from("web:monitor", [1, 2], now=0.0)
    tracker.heard_from("web:tablet", [2], now=0.0)

    assert tracker.online_seats() == {1: 1, 2: 2}


def test_a_silent_screen_goes_after_the_timeout_and_not_before() -> None:
    tracker = ScreenTracker()
    tracker.heard_from("web:monitor", [1], now=0.0)

    # Exactly at the timeout the screen is still there (a heartbeat may be in flight).
    assert tracker.expire(now=SCREEN_TIMEOUT_SECONDS) is False
    assert tracker.online_seats() == {1: 1}

    assert tracker.expire(now=SCREEN_TIMEOUT_SECONDS + 0.5) is True
    assert tracker.online_seats() == {}
    assert tracker.screen_count() == 0


def test_a_screen_that_keeps_talking_never_expires() -> None:
    tracker = ScreenTracker()
    for second in range(0, 30, 2):
        tracker.heard_from("web:monitor", [1], now=float(second))
        assert tracker.expire(now=float(second) + 1.0) is False
