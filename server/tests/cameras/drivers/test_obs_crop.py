"""Tests for the OBS crop driver (cameras/drivers/obs_crop.py) against the fake OBS.

The bench (obs_bench.py) has a fake OBS with one 4K webcam ("Cam 4K"), a
real client connected to it, and the driver running its worker on the fake
clock. Every test here runs twice: once with the OBS on this PC (reached
through playout's connection) and once with the OBS on a trackside laptop
(the cameras module's own remote connection, WP13): a remote crop camera
must behave exactly like a local one. We give the driver what the arbiter would (velocities, stops,
presets) and then look at the Crop/Pad filter inside the fake OBS: that is
what the projector windows and the stream show.

Numbers to keep in mind: the source is 3840x2160; the default narrowest
window is half its width (1920x1080); the default speeds are 0.3 frame
widths per second for pan.
"""

from collections.abc import Iterator

import pytest

from baja_studio.cameras.crop_window import CropWindow, WindowTarget
from baja_studio.cameras.driver import Velocity
from baja_studio.core.supervisor import Supervisor
from tests.cameras.drivers.obs_bench import CAMERA, FILTER, GATEWAY_KINDS, ObsBench, crop_camera
from tests.conftest import FakeClock

# Edges that leave the middle 1920x1080 of the 4K picture: the narrowest window, centred.
CENTRE_1080P = {"left": 960, "right": 960, "top": 540, "bottom": 540}


@pytest.fixture(params=GATEWAY_KINDS)
def bench(fake_clock: FakeClock, request: pytest.FixtureRequest) -> Iterator[ObsBench]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    bench = ObsBench(fake_clock, supervisor, request.param)
    yield bench
    supervisor.stop_all()
    bench.close()


def running(bench: ObsBench) -> ObsBench:
    """Start the driver and wait until it has read the filter and measured the source."""
    bench.driver.connect()
    bench.soon(lambda: bench.state.get("status") == "ok")
    return bench


# --- connecting --------------------------------------------------------------------------


def test_connecting_adopts_the_crop_obs_already_has_and_changes_nothing(bench: ObsBench) -> None:
    # The operator left the filter at the middle 1080p of the picture.
    bench.add_filter(**CENTRE_1080P)

    running(bench)

    # The driver measured the source through the crop: 1920 + 960 + 960 = 3840.
    crop = bench.state["crop"]
    assert (crop["source_width"], crop["source_height"]) == (3840, 2160)
    assert (crop["x"], crop["y"], crop["w"], crop["h"]) == (0.25, 0.25, 0.5, 0.5)
    assert crop["magnification"] == 2.0
    assert bench.state["position"] == {"pan": 0.5, "tilt": 0.5, "zoom": 1.0}
    # And it wrote nothing: connecting must never move the picture on air.
    assert bench.count("SetSourceFilterSettings") == 0


def test_it_is_a_crop_camera_with_presets_and_no_auto_mode(bench: ObsBench) -> None:
    capabilities = bench.driver.capabilities()

    assert capabilities.kind.value == "crop"
    assert capabilities.preset_max == 127
    assert not capabilities.supports_auto


# --- moving ------------------------------------------------------------------------------


def test_a_held_pan_moves_the_filter_at_the_configured_speed(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    bench.config = crop_camera(speed_follows_zoom=False)  # speeds in frame fractions, as set
    running(bench)

    # Full pan right for 0.4 s at 0.3 frame widths per second: 0.12 of 3840 px.
    bench.driver.move(Velocity(pan=1.0), ttl_ms=1000)
    bench.step(0.4)
    bench.driver.stop()
    bench.step(0.2)

    assert bench.crop_in_obs()["left"] == pytest.approx(960 + 0.12 * 3840, abs=2)
    # The window keeps its size: 1920 px wide whatever the position.
    assert bench.visible_size() == (1920, 1080)
    assert bench.state["moving"] is False


def test_a_velocity_nobody_renews_stops_by_itself_after_its_ttl(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    bench.config = crop_camera(speed_follows_zoom=False)
    running(bench)

    # 300 ms of pan, then silence (a page that lost its Wi-Fi): it moves 300 ms only.
    bench.driver.move(Velocity(pan=1.0), ttl_ms=300)
    bench.step(1.0)

    assert bench.crop_in_obs()["left"] == pytest.approx(960 + 0.3 * 0.3 * 3840, abs=2)
    assert bench.state["moving"] is False


def test_the_filter_is_only_written_when_its_pixels_change(bench: ObsBench) -> None:
    # Start at the right edge, then hold "right": the window cannot go further.
    bench.add_filter(left=1920, right=0, top=540, bottom=540)
    running(bench)

    bench.driver.move(Velocity(pan=1.0), ttl_ms=1000)
    bench.step(0.6)

    assert bench.count("SetSourceFilterSettings") == 0


def test_zooming_in_stops_at_min_window_and_the_output_stays_16_by_9(bench: ObsBench) -> None:
    bench.add_filter()  # an empty Crop/Pad filter: nothing cut, the whole picture
    running(bench)
    assert bench.state["crop"]["magnification"] == 1.0

    bench.driver.move(Velocity(zoom=1.0), ttl_ms=2500)
    bench.step(2.6)  # 0.5 of the zoom range per second: the full range in 2 s and more

    assert bench.visible_size() == (1920, 1080)  # 1080p: never upscaled
    assert bench.state["crop"]["zoom"] == 1.0
    assert bench.state["crop"]["magnification"] == 2.0


def test_min_window_is_read_live_from_the_settings(bench: ObsBench) -> None:
    bench.add_filter()
    running(bench)

    # The operator allows a narrower window (0.25: 960 px) on Camera setup.
    bench.config = crop_camera(min_window=0.25)
    bench.driver.move(Velocity(zoom=1.0), ttl_ms=3000)
    bench.step(2.5)

    assert bench.visible_size() == (960, 540)


# --- presets and jumps ---------------------------------------------------------------------


def test_a_stored_preset_is_saved_and_a_recall_jumps_back_to_it(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    # Store preset 3 here; the driver hands the window to the registry to save.
    bench.driver.store_preset(3)
    bench.step(0.1)
    assert bench.saved_presets == [(3, CropWindow(0.5, 0.5, 0.5))]

    # The registry saved it in the camera's settings (as the service would).
    bench.config = crop_camera(windows=[{"n": 3, "centre_x": 0.5, "centre_y": 0.5, "width": 0.5}])
    bench.driver.place_window(WindowTarget(0.3, 0.3))
    bench.step(0.1)
    assert bench.crop_in_obs()["left"] != 960

    bench.driver.recall_preset(3)
    bench.step(0.1)

    assert bench.crop_in_obs()["left"] == 960  # back to the stored window at once


def test_recalling_a_preset_that_was_never_stored_says_so(bench: ObsBench) -> None:
    bench.add_filter()
    running(bench)

    bench.driver.recall_preset(7)
    bench.step(0.1)

    assert bench.state["last_error"] == "preset 8 is not stored"  # operators count from 1


def test_click_to_centre_moves_the_window_there_and_reset_shows_everything(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    bench.driver.place_window(WindowTarget(0.3, 0.4))
    bench.step(0.1)
    crop = bench.state["crop"]
    assert (crop["centre_x"], crop["centre_y"]) == (0.3, 0.4)
    assert bench.crop_in_obs()["left"] == round(0.3 * 3840 - 960)

    bench.driver.place_window(WindowTarget(whole=True))
    bench.step(0.1)
    assert bench.visible_size() == (3840, 2160)


# --- what goes wrong in OBS -----------------------------------------------------------------


def test_a_missing_filter_is_degraded_until_it_is_created(bench: ObsBench) -> None:
    bench.driver.connect()
    bench.soon(lambda: bench.state.get("status") == "degraded")
    assert "has no filter 'Baja crop'" in bench.state["status_reason"]
    assert "Create filter" in bench.state["status_reason"]

    # Camera setup's "Create filter" button; OBS announces it, and the camera comes up.
    bench.add_filter()

    bench.soon(lambda: bench.state.get("status") == "ok")


def test_a_source_obs_does_not_have_makes_the_camera_offline(bench: ObsBench) -> None:
    bench.config = crop_camera()
    bench.config.obs_source = "Typo cam"
    bench.driver.connect()

    bench.soon(lambda: bench.state.get("status") == "offline")
    assert bench.state["status_reason"] == (
        "OBS has no source 'Typo cam' (check the name on Camera setup)"
    )


def test_a_filter_with_relative_unticked_is_degraded_and_left_alone(bench: ObsBench) -> None:
    bench.add_filter(relative=False, cx=1920, cy=1080)
    bench.driver.connect()

    bench.soon(lambda: bench.state.get("status") == "degraded")
    assert "'Relative' unticked" in bench.state["status_reason"]
    assert bench.count("SetSourceFilterSettings") == 0


def test_a_filter_switched_off_in_obs_is_degraded_but_still_moves(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    bench.obs.set_filter_enabled(CAMERA, FILTER, False)
    bench.soon(lambda: bench.state.get("status") == "degraded")
    assert "switched off in OBS" in bench.state["status_reason"]
    # The source still measures 3840 wide: a disabled filter cuts nothing.
    assert bench.state["crop"]["source_width"] == 3840


def test_obs_quitting_makes_the_camera_offline_and_it_measures_again_when_back(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    screenshots_before = bench.full_size_screenshots()

    bench.quit_obs()
    bench.soon(lambda: bench.state.get("status") == "offline")
    # Playout's words for its OBS ("OBS offline: OBS closed the connection"), or
    # the laptop's OBS named ("OBS at obs.test:4455 closed the connection ...").
    assert bench.state["status_reason"].startswith(bench.obs_away_reason)
    # A move while OBS is away does nothing and is not replayed later.
    bench.driver.move(Velocity(pan=1.0), ttl_ms=300)
    bench.step(0.5)

    bench.restart_obs()
    bench.soon(lambda: bench.state.get("status") == "ok")
    assert bench.full_size_screenshots() == screenshots_before + 1
    assert bench.crop_in_obs()["left"] == 960
    assert bench.count("SetSourceFilterSettings") == 0


def test_a_new_resolution_is_trusted_at_once_and_confirmed_once_the_camera_reopened(
    bench: ObsBench,
) -> None:
    bench.obs.change_video_size(CAMERA, 1920, 1080)
    bench.step(1.0)  # the camera has reopened at 1080p before the studio looks
    bench.add_filter()
    running(bench)
    assert bench.state["crop"]["source_width"] == 1920
    measured_before = bench.full_size_screenshots()

    # The operator switches the webcam to 4K: its settings name the resolution,
    # so the studio uses it at once, while the device takes 0.5 s to reopen.
    bench.obs.change_video_size(CAMERA, 3840, 2160)
    bench.soon(lambda: bench.state["crop"]["source_width"] == 3840, within_seconds=0.2)

    # It keeps measuring until a screenshot agrees (the first ones are still 1080p).
    bench.soon(lambda: bench.state.get("status") == "ok" and not bench.driver_waiting())
    assert bench.full_size_screenshots() >= measured_before + 2
    assert bench.state["crop"]["source_width"] == 3840


def test_a_new_resolution_its_settings_do_not_name_is_measured_again_later(
    bench: ObsBench,
) -> None:
    bench.add_filter()
    running(bench)

    # A camera left at "Device default": the event names no resolution, and the
    # first measurement still sees the old 4K picture (the device is reopening).
    bench.obs.change_video_size(CAMERA, 1920, 1080, named_in_settings=False)
    bench.step(0.4)
    assert bench.state["crop"]["source_width"] == 3840

    # The checks a second and three seconds later find the new size.
    bench.soon(lambda: bench.state["crop"]["source_width"] == 1920, within_seconds=4.0)


def test_the_size_is_checked_again_now_and_then_at_rest(bench: ObsBench) -> None:
    bench.add_filter()
    running(bench)
    measured = bench.full_size_screenshots()

    bench.step(31.0)

    assert bench.full_size_screenshots() == measured + 1


def test_a_smaller_resolution_under_a_crop_that_no_longer_fits_is_put_right(
    bench: ObsBench,
) -> None:
    # A 1080p window on the right of the 4K picture: 1920 px cut from the left.
    bench.add_filter(left=1920, right=0, top=540, bottom=540)
    running(bench)

    # The camera drops to 1080p. Cutting 1920 px from a 1920 px picture would
    # leave OBS's 1x1 minimum on air, so the driver re-expresses the window for
    # the resolution the source's settings name at once, then confirms it.
    bench.obs.change_video_size(CAMERA, 1920, 1080)
    bench.soon(lambda: bench.state["crop"]["source_width"] == 1920, within_seconds=0.2)

    bench.soon(lambda: bench.state.get("status") == "ok" and not bench.driver_waiting())
    bench.step(0.1)  # OBS renders the next frame
    assert bench.state["crop"]["source_width"] == 1920
    assert bench.visible_size() == (960, 540)  # the same half-width window, on 1080p
    assert bench.state["crop"]["x"] == pytest.approx(0.5, abs=0.01)  # still on the right half


def test_a_shrunk_picture_whose_resolution_is_not_named_is_reported_and_reset_mends_it(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    # "Device default" drops to 1080p: no size to go by, and OBS shows one pixel.
    bench.obs.change_video_size(CAMERA, 1920, 1080, named_in_settings=False)
    bench.soon(lambda: bench.state.get("status") == "degraded", within_seconds=5.0)
    assert "crop is larger than the picture" in bench.state["status_reason"]
    assert "Reset crop" in bench.state["status_reason"]

    # Camera setup's Reset crop: nothing cut, the size can be measured again.
    bench.driver.place_window(WindowTarget(whole=True))
    bench.soon(lambda: bench.state.get("status") == "ok", within_seconds=5.0)
    bench.step(0.1)
    assert bench.visible_size() == (1920, 1080)


def test_another_filter_that_changes_the_size_makes_the_camera_degraded(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    bench.operator.create_source_filter(CAMERA, "Scale", "scale_filter", {})

    bench.soon(lambda: bench.state.get("status") == "degraded")
    assert "another filter that changes its size ('Scale')" in bench.state["status_reason"]


# --- edits made in OBS, and our own echoes ---------------------------------------------------


def edit_in_obs(bench: ObsBench, **edges: int) -> None:
    """The operator changes the filter's values in OBS's dialog."""
    bench.obs.edit_filter_by_hand(CAMERA, FILTER, **edges)


def test_a_crop_changed_by_hand_in_obs_is_adopted(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    edit_in_obs(bench, left=0, right=1920)

    bench.soon(lambda: bench.state["crop"]["x"] == 0.0, within_seconds=0.2)


def test_an_edit_in_obs_right_after_a_move_is_checked_once_the_echo_window_passed(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    bench.driver.place_window(WindowTarget(0.4, 0.5))
    bench.step(0.04)  # the studio has just written

    # Within 0.5 s of that write an event may be a late echo: not trusted yet...
    edit_in_obs(bench, left=0, right=1920)
    bench.step(0.2)
    assert bench.state["crop"]["centre_x"] == 0.4
    # ...but the filter is read again once the window has passed, and the edit shows.
    bench.soon(lambda: bench.state["crop"]["x"] == 0.0, within_seconds=0.6)


def test_while_the_stick_moves_the_crop_an_edit_in_obs_is_overwritten(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    bench.config = crop_camera(speed_follows_zoom=False)
    running(bench)

    bench.driver.move(Velocity(pan=1.0), ttl_ms=1000)
    bench.step(0.2)
    edit_in_obs(bench, left=0, right=1920)  # someone drags the crop meanwhile
    bench.step(0.2)
    bench.driver.stop()
    bench.step(1.0)

    # The stick's path went on from where the stick had it: 0.4 s at 0.3 frames/s.
    assert bench.crop_in_obs()["left"] == pytest.approx(960 + 0.12 * 3840, abs=2)


def test_an_event_equal_to_what_was_written_never_reads_the_filter_again(
    bench: ObsBench,
) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    bench.driver.place_window(WindowTarget(0.4, 0.5))
    bench.step(1.0)
    reads_before = bench.count("GetSourceFilterList")

    # Long after the write, the operator opens the filter dialog and presses
    # Cancel: OBS announces the values the studio wrote.
    edit_in_obs(bench, **{key: bench.crop_in_obs()[key] for key in CENTRE_1080P})
    bench.step(1.0)

    assert bench.count("GetSourceFilterList") == reads_before


def test_the_echo_of_our_own_writes_is_not_taken_for_an_edit(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    reads_before = bench.count("GetSourceFilterList")

    # Every write comes back as a SourceFilterSettingsChanged event.
    bench.driver.move(Velocity(pan=1.0, tilt=0.5), ttl_ms=1000)
    bench.step(0.4)

    # Had an echo looked like an edit, the driver would have read the filter again.
    assert bench.count("SetSourceFilterSettings") >= 5
    assert bench.count("GetSourceFilterList") == reads_before


# --- measuring at the right moment -------------------------------------------------------------


def test_a_measurement_waits_until_obs_has_shown_the_last_write(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    bench.driver.place_window(WindowTarget(0.3, 0.5))
    bench.step(0.04)  # written at this very moment; OBS shows it from its next frame

    # At the same moment, something makes the driver measure again (the
    # operator ticks the filter's eye, already ticked).
    bench.obs.set_filter_enabled(CAMERA, FILTER, True)
    bench.soon(lambda: not bench.driver_waiting())

    # Measured too early, the old picture plus the new edges would be the wrong size.
    assert bench.state["crop"]["source_width"] == 3840
    assert bench.state["crop"]["source_height"] == 2160


def test_motion_waiting_for_a_measurement_does_not_catch_up_afterwards(bench: ObsBench) -> None:
    bench.add_filter(left=0, right=1920, top=540, bottom=540)  # room to pan right
    bench.config = crop_camera(speed_follows_zoom=False)
    running(bench)

    bench.driver.move(Velocity(pan=1.0), ttl_ms=2000)
    bench.step(0.2)
    bench.obs.set_filter_enabled(CAMERA, FILTER, True)  # a measurement is needed
    bench.step(0.8)
    bench.driver.stop()
    bench.step(0.2)

    # Held for 1.0 s, but the window stood still while the size was measured:
    # it moved less than 1.0 s worth (0.3 frames/s), and never jumped.
    moved = bench.crop_in_obs()["left"]
    assert 0.2 * 0.3 * 3840 < moved < 0.85 * 0.3 * 3840


# --- speed and settings ------------------------------------------------------------------------


def test_zoomed_in_the_window_moves_slower_so_the_picture_moves_as_fast(bench: ObsBench) -> None:
    # At 2x the window is half as wide: by default it pans half as many frame
    # fractions per second, which on air looks the same speed as at 1x.
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    bench.driver.move(Velocity(pan=1.0), ttl_ms=400)
    bench.step(1.0)

    assert bench.crop_in_obs()["left"] == pytest.approx(960 + 0.5 * 0.12 * 3840, abs=2)


def test_a_settings_edit_does_not_move_the_picture_by_itself(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)  # a 1920 px window
    running(bench)

    # The operator raises min_window above the current window on Camera setup.
    bench.config = crop_camera(min_window=0.75)
    bench.step(2.0)
    assert bench.count("SetSourceFilterSettings") == 0

    # The next move applies it.
    bench.driver.move(Velocity(pan=0.1), ttl_ms=100)
    bench.step(0.3)
    assert bench.visible_size()[0] == 2880
