"""Tests for the image-control rules (imaging/controls.py): plain functions, no devices.

A `ControlState` is one control as read from a camera: its value, whether it
is on automatic (None: it has no automatic mode), its range and step, and the
device's default. The rules turn operator buttons into changes to write.
"""

from baja_studio.agents.contract import ImageControlValue
from baja_studio.imaging.config import ImageSetting
from baja_studio.imaging.controls import (
    ControlState,
    all_auto_changes,
    change_problem,
    copy_changes,
    default_changes,
    lock_changes,
    ordered,
    saved_after_write,
    snap,
)


def exposure(value: float = -6, auto: bool | None = True) -> ControlState:
    """A webcam's exposure: -13..-1 in steps of 1, default -6 on auto."""
    return ControlState(value, auto, -13, -1, 1, default=-6)


def white_balance(value: float = 4600, auto: bool | None = True) -> ControlState:
    """White balance in kelvin, steps of 10."""
    return ControlState(value, auto, 2800, 6500, 10, default=4600)


def gain(value: float = 0) -> ControlState:
    """Gain has no automatic mode on a webcam (auto None)."""
    return ControlState(value, None, 0, 100, 1, default=0)


def test_values_are_clamped_and_put_on_the_step() -> None:
    assert snap(4613, white_balance()) == 4610
    assert snap(9000, white_balance()) == 6500
    assert snap(-40, exposure()) == -13


def test_a_value_outside_the_range_is_refused_with_the_range_in_the_message() -> None:
    # Refused, not clamped: someone typing -20 should see that it did not apply.
    assert change_problem(exposure(), ImageSetting(value=-20)) == "-20 is outside -13..-1"
    assert change_problem(exposure(), ImageSetting(value=-7, auto=False)) == ""


def test_auto_on_a_control_without_an_automatic_mode_is_refused() -> None:
    assert change_problem(gain(), ImageSetting(auto=True)) == "this control has no automatic mode"


def test_lock_turns_every_automatic_control_into_manual_at_its_current_value() -> None:
    # The camera chose exposure -4 and 5200 K by itself; Lock keeps exactly those.
    controls = {
        "exposure": exposure(-4),
        "white_balance": white_balance(5200),
        "gain": gain(12),
        "focus": ControlState(30, False, 0, 1023, 1),
    }

    assert lock_changes(controls) == {
        "exposure": ImageSetting(value=-4, auto=False),
        "white_balance": ImageSetting(value=5200, auto=False),
    }


def test_all_auto_touches_only_controls_that_have_an_automatic_mode() -> None:
    controls = {"exposure": exposure(-4, auto=False), "gain": gain(12)}

    assert all_auto_changes(controls) == {"exposure": ImageSetting(auto=True)}


def test_reset_uses_the_device_defaults_and_skips_controls_without_one() -> None:
    # Agent controls have no default (the contract gives none), so they are skipped.
    controls = {
        "exposure": exposure(-3, auto=False),
        "gain": gain(40),
        "agent_only": ControlState(5, None, 0, 10, 1),
    }

    assert default_changes(controls) == {
        "exposure": ImageSetting(value=-6, auto=True),
        "gain": ImageSetting(value=0, auto=None),
    }


def test_copy_takes_only_shared_controls_and_fits_them_to_the_target() -> None:
    # The source's white balance is on manual at 7000 K, beyond the target's
    # 6500 K top; it is clamped. Its exposure is on auto, so auto is copied.
    # Its gain does not exist on the target, so it is skipped.
    source = {
        "exposure": exposure(-4, auto=True),
        "white_balance": ControlState(7000, False, 2000, 9000, 1),
        "gain": gain(30),
    }
    target = {"exposure": exposure(-8, auto=False), "white_balance": white_balance(4000, False)}

    changes, skipped = copy_changes(source, target)

    assert changes == {
        "exposure": ImageSetting(auto=True),
        "white_balance": ImageSetting(value=6500, auto=False),
    }
    assert skipped == ["gain"]


def test_copy_to_a_control_without_auto_sends_the_value_only() -> None:
    changes, _ = copy_changes({"gain": gain(33)}, {"gain": gain(0)})

    assert changes == {"gain": ImageSetting(value=33, auto=None)}


def test_what_is_saved_after_a_write_is_auto_or_the_value_read_back() -> None:
    assert saved_after_write(exposure(-5, auto=True)) == ImageSetting(auto=True)
    assert saved_after_write(exposure(-5, auto=False)) == ImageSetting(value=-5, auto=False)
    assert saved_after_write(gain(7)) == ImageSetting(value=7, auto=None)


def test_controls_are_listed_in_display_order_then_alphabetically() -> None:
    assert ordered(["zzz", "gain", "exposure", "aaa"]) == ["exposure", "gain", "aaa", "zzz"]


def test_agent_controls_become_control_states_without_defaults() -> None:
    value = ImageControlValue(value=-6, auto=True, locked=False, min=-13, max=-1, step=1)

    state = ControlState.from_agent(value)

    assert (state.value, state.auto, state.min, state.max, state.default) == (
        -6,
        True,
        -13,
        -1,
        None,
    )
