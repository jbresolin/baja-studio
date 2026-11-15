"""Tests for the OBS crop driver (cameras/drivers/obs_crop.py) against the fake OBS.

The bench (obs_bench.py) has a fake OBS with one 4K webcam ("Cam 4K"), a
real client connected to it, and the driver running its worker on the fake
clock. We give the driver what the arbiter would (velocities, stops,
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
from tests.cameras.drivers.obs_bench import CAMERA, FILTER, ObsBench, crop_camera
from tests.conftest import FakeClock

# Edges that leave the middle 1920x1080 of the 4K picture: the narrowest window, centred.
CENTRE_1080P = {"left": 960, "right": 960, "top": 540, "bottom": 540}


@pytest.fixture
def bench(fake_clock: FakeClock) -> Iterator[ObsBench]:
    supervisor = Supervisor(fake_clock)
    supervisor.register_module("cameras")
    supervisor.start_module("cameras", [])
    bench = ObsBench(fake_clock, supervisor)
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

    bench.driver.move(Velocity(zoom=1.0), ttl_ms=2000)
    bench.step(2.0)  # 0.5 of the zoom range per second: the full range in 2 s and more

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
    assert bench.state["status_reason"] == "OBS offline: OBS closed the connection"
    # A move while OBS is away does nothing and is not replayed later.
    bench.driver.move(Velocity(pan=1.0), ttl_ms=300)
    bench.step(0.5)

    bench.restart_obs()
    bench.soon(lambda: bench.state.get("status") == "ok")
    assert bench.full_size_screenshots() == screenshots_before + 1
    assert bench.crop_in_obs()["left"] == 960
    assert bench.count("SetSourceFilterSettings") == 0


def test_a_new_resolution_is_measured_again(bench: ObsBench) -> None:
    bench.obs.change_video_size(CAMERA, 1920, 1080)
    bench.add_filter()
    running(bench)
    assert bench.state["crop"]["source_width"] == 1920

    # The operator switches the webcam to 4K in its OBS properties.
    bench.obs.change_video_size(CAMERA, 3840, 2160)

    bench.soon(lambda: bench.state["crop"]["source_width"] == 3840)


def test_a_smaller_resolution_under_a_crop_that_no_longer_fits_is_put_right(
    bench: ObsBench,
) -> None:
    # A 1080p window on the right of the 4K picture: 1920 px cut from the left.
    bench.add_filter(left=1920, right=0, top=540, bottom=540)
    running(bench)

    # The camera drops to 1080p: cutting 1920 px from a 1920 px picture leaves
    # nothing, so OBS shows black. The driver re-expresses the window for the
    # resolution the source's settings name, then measures again.
    bench.obs.change_video_size(CAMERA, 1920, 1080)

    bench.soon(lambda: (bench.state.get("crop") or {}).get("source_width") == 1920)
    bench.soon(lambda: bench.state.get("status") == "ok")
    width, height = bench.visible_size()
    assert width > 0 and height > 0
    assert bench.state["crop"]["x"] == pytest.approx(0.5, abs=0.01)  # still on the right half


def test_a_crop_changed_by_hand_in_obs_is_adopted(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)

    # Someone drags the crop in OBS's filter dialog: OBS announces new settings.
    bench.obs._request_SetSourceFilterSettings(
        {"sourceName": CAMERA, "filterName": FILTER, "filterSettings": {"left": 0, "right": 1920}}
    )

    bench.soon(lambda: bench.state["crop"]["x"] == 0.0)


def test_the_echo_of_our_own_writes_is_not_taken_for_an_edit(bench: ObsBench) -> None:
    bench.add_filter(**CENTRE_1080P)
    running(bench)
    reads_before = bench.count("GetSourceFilter")

    # Every write comes back as a SourceFilterSettingsChanged event.
    bench.driver.move(Velocity(pan=1.0, tilt=0.5), ttl_ms=1000)
    bench.step(0.4)

    # Had an echo looked like an edit, the driver would have read the filter again.
    assert bench.count("SetSourceFilterSettings") >= 5
    assert bench.count("GetSourceFilter") == reads_before
