"""Tests for where a seat's microphone is heard (audio/seat_states.py).

Each case fills a `MixerCache` the way the mixer's answers would, for one
channel ("1") and the production bus ("bus5"), then asks the rules. A value
left out is one the mixer has not told us yet.

Taps: 3 is "pre-fader" (the fader does not matter), 4 is "post-fader"
(the fader must be up for the send to be heard).
"""

from typing import Any

import pytest

from baja_studio.audio.mixer_cache import MixerCache
from baja_studio.audio.seat_states import (
    LIVE,
    MUTED,
    NOT_MAPPED,
    TO_PRODUCTION,
    all_of,
    production_to_seat,
    reaches_bus,
    reaches_lr,
    seat_state,
)

PRE, POST = 3, 4


def cache_with(
    muted: bool | None = False,
    fader: float | None = 0.75,
    lr: bool | None = True,
    send: float | None = 0.75,
    send_on: bool | None = True,
    tap: int | None = PRE,
    channel: str = "1",
    bus: str = "bus5",
) -> MixerCache:
    """A cache for one channel and one send; None means "not reported yet"."""
    cache = MixerCache()
    values: list[tuple[dict[Any, Any], Any, Any]] = [
        (cache.mutes, channel, muted),
        (cache.faders, channel, fader),
        (cache.lr_assigns, channel, lr),
        (cache.sends, (channel, bus), send),
        (cache.send_ons, (channel, bus), send_on),
        (cache.send_taps, (channel, bus), tap),
    ]
    for table, key, value in values:
        if value is not None:
            table[key] = value
    return cache


def test_three_valued_and_lets_one_known_no_win_over_unknowns() -> None:
    assert all_of(True, True) is True
    assert all_of(True, None) is None
    assert all_of(None, False) is False


@pytest.mark.parametrize(
    ("cache", "expected"),
    [
        (cache_with(), True),
        (cache_with(muted=True), False),
        (cache_with(lr=False), False),
        (cache_with(fader=0.0), False),
        # Not told the mute yet: cannot say.
        (cache_with(muted=None), None),
        (cache_with(lr=None), None),
        # ...unless something else already says no.
        (cache_with(muted=None, fader=0.0), False),
    ],
)
def test_a_channel_reaches_lr_when_unmuted_assigned_and_its_fader_is_up(
    cache: MixerCache, expected: bool | None
) -> None:
    assert reaches_lr(cache, "1") is expected


@pytest.mark.parametrize(
    ("cache", "expected"),
    [
        (cache_with(), True),
        (cache_with(muted=True), False),
        (cache_with(send=0.0), False),
        (cache_with(send_on=False), False),
        # A send-on switch the mixer never reported counts as on (the XR18 may lack it).
        (cache_with(send_on=None), True),
        # Pre-fader: the fader does not matter; post-fader: it must be up.
        (cache_with(fader=0.0, tap=PRE), True),
        (cache_with(fader=0.0, tap=POST), False),
        (cache_with(fader=0.75, tap=POST), True),
        # Tap unknown: an open fader makes it irrelevant, a closed one leaves it unknown.
        (cache_with(fader=0.75, tap=None), True),
        (cache_with(fader=0.0, tap=None), None),
        (cache_with(send=None), None),
    ],
)
def test_a_channel_reaches_a_bus_when_unmuted_and_its_send_is_on_and_up(
    cache: MixerCache, expected: bool | None
) -> None:
    assert reaches_bus(cache, "1", "bus5") is expected


@pytest.mark.parametrize(
    ("cache", "expected"),
    [
        # Open on the main mix: the host is on air.
        (cache_with(), LIVE),
        # What "talk to production" is meant to leave: off LR (LR assign off)
        # but still open, pre-fader, into the production bus.
        (cache_with(lr=False), TO_PRODUCTION),
        # The same with the main fader closed and a pre-fader send.
        (cache_with(fader=0.0, tap=PRE), TO_PRODUCTION),
        # Closed fader with a post-fader send: heard nowhere.
        (cache_with(fader=0.0, tap=POST), MUTED),
        (cache_with(muted=True), MUTED),
        (cache_with(lr=False, send=0.0), MUTED),
        # Nothing known yet: no state rather than a guess.
        (MixerCache(), None),
        (cache_with(lr=False, send=None), None),
    ],
)
def test_a_seat_is_live_to_production_or_muted_from_the_mixer_values(
    cache: MixerCache, expected: str | None
) -> None:
    assert seat_state(cache, "1", "bus5") == expected


def test_a_seat_without_a_microphone_channel_is_not_mapped() -> None:
    assert seat_state(MixerCache(), None, "bus5") == NOT_MAPPED


def test_production_to_you_is_any_production_mic_heard_in_the_seats_in_ears() -> None:
    # PROD 1 (channel 5) is open into bus1 (seat 1's in-ears); PROD 2 is not.
    cache = cache_with(channel="5", bus="bus1")
    cache.mutes["6"] = False
    cache.faders["6"] = 0.75
    cache.sends[("6", "bus1")] = 0.0

    assert production_to_seat(cache, ["5", "6"], "bus1") is True
    assert production_to_seat(cache, ["6"], "bus1") is False
    # Nothing known about a mic counts as no, never as a guess.
    assert production_to_seat(MixerCache(), ["5"], "bus1") is False
