"""Tests for telling the studio's own writes from edits made in OBS (obs_crop_edits.py).

OBS announces every change of the filter, ours included. `EditWatch` is
told the time, so these tests pass plain numbers.
"""

from baja_studio.cameras.crop_window import PixelCrop
from baja_studio.cameras.drivers.obs_crop_edits import WRITE_PROTECTION_SECONDS, EditWatch

OURS, THEIRS = PixelCrop(960, 540, 960, 540), PixelCrop(0, 540, 1920, 540)


def test_an_echo_of_what_we_wrote_never_needs_a_read() -> None:
    watch = EditWatch()
    watch.wrote(OURS, now=1.0)

    assert watch.settings_event(OURS, now=1.01) is False
    # Even long after (the filter dialog opened and cancelled).
    assert watch.settings_event(OURS, now=60.0) is False


def test_a_different_value_soon_after_a_write_is_read_again_once_the_window_passed() -> None:
    watch = EditWatch()
    watch.wrote(OURS, now=1.0)

    assert watch.settings_event(THEIRS, now=1.2) is False  # maybe a late echo
    assert not watch.read_again_due(1.3)
    assert watch.read_again_at() == 1.0 + WRITE_PROTECTION_SECONDS
    assert watch.read_again_due(1.0 + WRITE_PROTECTION_SECONDS)
    assert not watch.read_again_due(2.0)  # only once


def test_a_different_value_after_the_window_is_an_edit_made_in_obs() -> None:
    watch = EditWatch()
    watch.wrote(OURS, now=1.0)

    assert watch.settings_event(THEIRS, now=1.0 + WRITE_PROTECTION_SECONDS) is True
