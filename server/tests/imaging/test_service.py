"""Tests for the imaging service (imaging/service.py) through the running cameras module.

The `studio` fixture runs the cameras module with an in-process UVC child
over the fake duvc library: two S600 webcams (same name, different USB
ports) and a Pixy (tests/imaging/uvc_harness.py). Commands are sent the way
the Image page sends them; the checks read the card the page draws
(`state.cameras.image.<camera>`), the saved settings, and the fake devices.
"""

import pytest

from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_duvc import PIXY_PATH, S600_A_PATH, S600_B_PATH
from tests.conftest import step_fake_time
from tests.imaging.uvc_harness import image_card, pixy_camera, s600_camera, saved_image
from tests.modules.camera_studio import CAMERA_CLOCK_WORKERS, Studio, smtav


def two_s600s(studio: Studio) -> None:
    """Configure both S600s and wait until both cards have been read."""
    studio.configure(
        cameras=[s600_camera("s600-a", S600_A_PATH), s600_camera("s600-b", S600_B_PATH, 1)]
    )
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")
    studio.soon(lambda: image_card(studio, "s600-b").get("status") == "ok")


def test_a_usb_camera_gets_a_card_with_every_control_and_its_range(studio: Studio) -> None:
    two_s600s(studio)

    card = image_card(studio, "s600-a")

    assert card["source"] == "usb"
    assert card["device"]["present"] is True
    # Ranges come from the device, never from our code.
    assert card["controls"]["exposure"] == {
        "value": -6,
        "auto": True,
        "min": -13,
        "max": -1,
        "step": 1,
        "default": -6,
        "error": "",
    }
    # Gain has no automatic mode: auto is None (the page shows no Auto box).
    assert card["controls"]["gain"]["auto"] is None


def test_a_write_applies_at_once_and_what_the_device_took_is_saved(studio: Studio) -> None:
    two_s600s(studio)

    studio.command(
        "set_image", camera="s600-a", controls={"exposure": {"auto": False, "value": -9}}
    )

    exposure = studio.usb.control(S600_A_PATH, "Exposure")
    assert exposure.value == -9 and exposure.mode.name == "Manual"
    assert image_card(studio, "s600-a")["controls"]["exposure"]["value"] == -9
    assert saved_image(studio, "s600-a") == {"exposure": {"value": -9, "auto": False}}
    # The other S600 (same name, other port) was not touched.
    assert studio.usb.control(S600_B_PATH, "Exposure").mode.name == "Auto"


def test_a_value_that_does_not_apply_is_shown_on_its_control_and_not_saved(
    studio: Studio,
) -> None:
    two_s600s(studio)

    result = studio.command(
        "set_image", camera="s600-a", controls={"gain": {"value": 500}, "contrast": {"value": 70}}
    )

    # Gain 500 is beyond 0..100: refused and shown. Contrast still applied.
    assert result["controls"]["gain"]["error"] == "500 is outside 0..100"
    assert result["controls"]["contrast"]["value"] == 70
    assert saved_image(studio, "s600-a") == {"contrast": {"value": 70, "auto": None}}

    # The error stays until that control is written successfully.
    studio.command("set_image", camera="s600-a", controls={"gain": {"value": 20}})
    assert image_card(studio, "s600-a")["controls"]["gain"]["error"] == ""


def test_a_refusal_by_the_device_itself_is_shown_on_the_control(studio: Studio) -> None:
    # The page never offers this, but a device can refuse what we think is
    # valid: here the fake device is told its gain range shrank meanwhile.
    two_s600s(studio)
    studio.usb.control(S600_A_PATH, "Gain").range.max = 10

    result = studio.command("set_image", camera="s600-a", controls={"gain": {"value": 50}})

    # The library's words are generic; the studio says what was refused.
    assert result["controls"]["gain"]["error"] == (
        "the camera refused 50: Failed to set camera property"
    )
    assert saved_image(studio, "s600-a") == {}


def test_auto_where_there_is_none_and_unknown_controls_are_refused(studio: Studio) -> None:
    two_s600s(studio)

    result = studio.command("set_image", camera="s600-a", controls={"gain": {"auto": True}})
    assert result["controls"]["gain"]["error"] == "this control has no automatic mode"

    with pytest.raises(CommandError, match="has no image control smell; it has exposure, gain"):
        studio.command("set_image", camera="s600-a", controls={"smell": {"value": 1}})


def test_lock_keeps_what_the_camera_chose_and_it_stops_drifting(studio: Studio) -> None:
    # The light changes during the race; exposure and white balance follow it
    # on auto. Lock freezes them at what the camera chose at that moment.
    two_s600s(studio)
    studio.usb.library.set_scene(0.8)
    chosen_exposure = studio.usb.control(S600_A_PATH, "Exposure").read().value

    result = studio.command("lock_image", camera="s600-a")

    assert result["locked"] == ["exposure", "focus", "white_balance"]
    assert result["controls"]["exposure"] == {
        **result["controls"]["exposure"],
        "value": chosen_exposure,
        "auto": False,
    }
    assert saved_image(studio, "s600-a")["exposure"] == {"value": chosen_exposure, "auto": False}
    studio.usb.library.set_scene(0.1)  # a cloud
    assert studio.usb.control(S600_A_PATH, "Exposure").read().value == chosen_exposure


def test_back_to_auto_is_saved_as_auto(studio: Studio) -> None:
    two_s600s(studio)
    studio.command("lock_image", camera="s600-a")

    studio.command("set_image", camera="s600-a", controls={"exposure": {"auto": True}})

    assert saved_image(studio, "s600-a")["exposure"] == {"value": None, "auto": True}
    assert studio.usb.control(S600_A_PATH, "Exposure").mode.name == "Auto"


def test_reset_puts_the_device_defaults_back_and_forgets_the_saved_settings(
    studio: Studio,
) -> None:
    two_s600s(studio)
    studio.command(
        "set_image",
        camera="s600-a",
        controls={"exposure": {"auto": False, "value": -2}, "contrast": {"value": 90}},
    )

    result = studio.command("reset_image", camera="s600-a")

    assert result["controls"]["exposure"]["auto"] is True
    assert result["controls"]["contrast"]["value"] == 50
    assert saved_image(studio, "s600-a") == {}


def test_copy_takes_the_controls_both_have_clamped_to_the_target(studio: Studio) -> None:
    # Make the second S600 a little different: its white balance stops at 6000 K.
    two_s600s(studio)
    studio.usb.control(S600_B_PATH, "WhiteBalance").range.max = 6000
    studio.command("get_image", camera="s600-b")  # read the new range
    studio.command(
        "set_image",
        camera="s600-a",
        controls={"white_balance": {"auto": False, "value": 6400}, "gamma": {"value": 220}},
    )

    result = studio.command("copy_image", **{"from": "s600-a", "to": "s600-b"})

    assert result["controls"]["white_balance"] == {
        **result["controls"]["white_balance"],
        "value": 6000,
        "auto": False,
    }
    assert result["controls"]["gamma"]["value"] == 220
    assert result["skipped"] == []
    assert saved_image(studio, "s600-b")["white_balance"] == {"value": 6000, "auto": False}


def test_copying_from_the_pixy_never_touches_pan_or_tilt(studio: Studio) -> None:
    # Pan, tilt and zoom are motion, not image: they are not in the card at all.
    studio.configure(cameras=[s600_camera(), pixy_camera()])
    studio.soon(lambda: image_card(studio, "pixy").get("status") == "ok")
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")

    result = studio.command("copy_image", **{"from": "pixy", "to": "s600-a"})

    assert "pan" not in image_card(studio, "pixy")["controls"]
    assert "pan" not in result["copied"]
    assert not [write for write in studio.usb.library.writes if write[0] == PIXY_PATH]


def test_cameras_without_image_settings_are_refused_with_what_to_do(studio: Studio) -> None:
    studio.network.add("10.0.0.9")
    studio.configure(cameras=[smtav()])

    with pytest.raises(CommandError, match="pick its USB device on the Camera setup page"):
        studio.command("get_image", camera="smtav")
    with pytest.raises(CommandError, match="no enabled camera called 'nope'"):
        studio.command("get_image", camera="nope")
    with pytest.raises(CommandError, match="pick another camera"):
        studio.command("copy_image", **{"from": "smtav", "to": "smtav"})


def test_an_unplugged_camera_is_refused_and_its_card_says_why(studio: Studio) -> None:
    two_s600s(studio)
    studio.usb.unplug(S600_A_PATH)

    with pytest.raises(CommandError, match="not plugged in"):
        studio.command("set_image", camera="s600-a", controls={"gain": {"value": 3}})
    card = image_card(studio, "s600-a")
    assert card["status"] == "offline"
    assert "not plugged in" in card["status_reason"]
    # The last known controls stay on the card, so the page can still show them.
    assert "gain" in card["controls"]


def test_a_dragged_slider_is_saved_once_when_the_drag_is_over(studio: Studio) -> None:
    # Ten writes a tenth of a second apart: the camera follows each, but the
    # settings (and their version, which Camera setup watches) change once.
    two_s600s(studio)
    version_before = studio.services.settings.record("cameras").version

    for value in range(40, 50):
        studio.command("set_image", camera="s600-a", controls={"gain": {"value": value}})
        step_fake_time(studio.clock, 0.1, CAMERA_CLOCK_WORKERS)
    assert studio.usb.control(S600_A_PATH, "Gain").value == 49

    assert saved_image(studio, "s600-a") == {"gain": {"value": 49, "auto": None}}
    assert studio.services.settings.record("cameras").version == version_before + 1


def test_a_preset_rename_during_a_drag_and_the_drag_s_save_keep_each_other(
    studio: Studio,
) -> None:
    # Two writers of the same camera list: neither may undo the other.
    studio.configure(cameras=[s600_camera(), pixy_camera()])
    studio.soon(lambda: image_card(studio, "pixy").get("status") == "ok")
    studio.command("set_image", camera="pixy", controls={"gain": {"value": 12}})

    studio.command("rename_preset", camera="pixy", n=0, label="Start gate")

    assert saved_image(studio, "pixy") == {"gain": {"value": 12, "auto": None}}
    pixy = studio.services.settings.get("cameras").cameras[1]  # type: ignore[attr-defined]
    assert [(preset.n, preset.label) for preset in pixy.presets] == [(0, "Start gate")]


def test_reading_back_after_a_write_reads_only_what_was_written(studio: Studio) -> None:
    # Each read is a round trip to the device; the others did not change.
    two_s600s(studio)
    studio.usb.library.calls.clear()

    studio.command("set_image", camera="s600-a", controls={"contrast": {"value": 60}})

    assert dict(studio.usb.library.calls) == {"set": 1, "get": 1, "get_range": 1}
