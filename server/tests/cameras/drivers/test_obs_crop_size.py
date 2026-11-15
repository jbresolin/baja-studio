"""Tests for when an OBS crop camera measures its source (cameras/drivers/obs_crop_size.py).

`SourceSize` is told the time, so these tests pass plain numbers. It answers
"measure now?" and remembers which size to trust.
"""

from baja_studio.cameras.drivers.obs_crop_size import (
    REMEASURE_AT_REST_SECONDS,
    RESOLUTION_WAIT_SECONDS,
    Measured,
    SourceSize,
)

FOUR_K, FULL_HD = (3840, 2160), (1920, 1080)


def known(size: tuple[int, int] = FOUR_K) -> SourceSize:
    """A tracker that measured `size` at time 0."""
    sizes = SourceSize()
    sizes.measured(size, 0.0)
    return sizes


def test_an_unknown_size_is_measured_but_only_once_the_filter_is_quiet() -> None:
    sizes = SourceSize()
    sizes.touched(10.0)  # the filter's edges just changed

    assert not sizes.due(10.1, at_rest=True)  # OBS may not show them yet
    assert sizes.due(10.25, at_rest=True)


def test_a_named_resolution_is_trusted_at_once_and_confirmed_by_measuring() -> None:
    sizes = known(FOUR_K)

    # The camera's settings now say 1080p: use it now (True: re-express the crop).
    assert sizes.settings_changed(FULL_HD, now=1.0) is True
    assert sizes.size == FULL_HD
    # A screenshot still showing 4K is the device still reopening: not yet.
    assert sizes.measured(FOUR_K, now=1.3) == Measured.NOT_YET
    assert sizes.needed
    # Once a screenshot agrees, it is settled.
    assert sizes.measured(FULL_HD, now=1.8) == Measured.SAME
    assert not sizes.needed


def test_after_the_wait_the_measurement_wins_over_the_named_resolution() -> None:
    sizes = known(FOUR_K)
    sizes.settings_changed(FULL_HD, now=0.0)

    outcome = sizes.measured(FOUR_K, now=RESOLUTION_WAIT_SECONDS + 0.1)

    assert outcome == Measured.CHANGED
    assert sizes.size == FOUR_K


def test_a_change_without_a_resolution_is_checked_again_later_at_rest() -> None:
    sizes = known(FOUR_K)
    sizes.settings_changed(None, now=0.0)
    sizes.measured(FOUR_K, now=0.3)  # still the old picture

    assert not sizes.due(0.5, at_rest=True)
    assert sizes.due(1.0, at_rest=True)  # the check a second later
    assert not sizes.due(1.0, at_rest=False)  # never while moving


def test_at_rest_the_size_is_checked_every_half_minute() -> None:
    sizes = known(FOUR_K)

    assert not sizes.due(REMEASURE_AT_REST_SECONDS - 1, at_rest=True)
    assert sizes.due(REMEASURE_AT_REST_SECONDS, at_rest=True)
