"""Tests for the fake OBS's seeded delays (fakes/obs_timing.py)."""

from baja_studio.fakes.obs_timing import ObsTiming, TimingRanges


def draws(timing: ObsTiming, input_name: str) -> list[float]:
    """A few delays of each kind for one input, in a fixed order."""
    return [*timing.opening(input_name), timing.seek(input_name), timing.event(input_name)]


def test_the_same_seed_gives_the_same_delays() -> None:
    # A race found with seed 17 must come back every time with seed 17.
    assert draws(ObsTiming(17), "a") == draws(ObsTiming(17), "a")
    assert draws(ObsTiming(17), "a") != draws(ObsTiming(18), "a")


def test_each_input_has_its_own_stream_of_delays() -> None:
    # Asking for input b's delays in between must not change input a's.
    alone = ObsTiming(5)
    mixed = ObsTiming(5)
    mixed.seek("b")
    mixed.event("b")

    assert draws(alone, "a") == draws(mixed, "a")


def test_delays_stay_inside_their_ranges_in_whole_milliseconds() -> None:
    ranges = TimingRanges()
    timing = ObsTiming(1, ranges)

    for _ in range(200):
        opening, first_frame = timing.opening("a")
        seek = timing.seek("a")
        for value, (low, high) in [
            (opening, ranges.opening_s),
            (first_frame, ranges.first_frame_s),
            (seek, ranges.seek_s),
        ]:
            assert low <= value <= high
            assert round(value, 3) == value
