"""Tests for the fake V4L2 system (fakes/fake_v4l2.py).

The fake stands in for the Linux webcam driver in the NanoPi node's tests,
so it must refuse what the driver refuses. These tests pin down the rules
it models (see its docstring for where each comes from).
"""

import errno

import pytest

from baja_studio.childprocs import uvc_v4l2 as ids
from baja_studio.fakes.fake_v4l2 import R3S_USB3_LINK, FakeV4l2System, main


def test_a_value_is_refused_while_its_automatic_partner_is_on() -> None:
    system = FakeV4l2System()
    device = system.open(R3S_USB3_LINK)
    # The camera starts with automatic white balance: the temperature is inactive.
    with pytest.raises(OSError) as refused:
        device.set_control(ids.CID_WHITE_BALANCE_TEMPERATURE, 5000)
    assert refused.value.errno == errno.EACCES
    device.set_control(ids.CID_AUTO_WHITE_BALANCE, 0)
    device.set_control(ids.CID_WHITE_BALANCE_TEMPERATURE, 5000)
    assert device.get_control(ids.CID_WHITE_BALANCE_TEMPERATURE) == 5000


def test_a_replug_makes_new_nodes_and_brings_the_factory_settings_back() -> None:
    system = FakeV4l2System()
    device = system.open(R3S_USB3_LINK)
    device.set_control(ids.CID_BRIGHTNESS, 20)
    [before] = system.capture_nodes()
    system.unplug(R3S_USB3_LINK)
    assert system.capture_nodes() == []
    # The handle opened before the unplug is dead now.
    with pytest.raises(OSError):
        device.get_control(ids.CID_BRIGHTNESS)
    system.plug(R3S_USB3_LINK)
    [after] = system.capture_nodes()
    assert after.stable_path == before.stable_path and after.identity != before.identity
    assert system.open(R3S_USB3_LINK).get_control(ids.CID_BRIGHTNESS) == 0


def test_an_unknown_control_is_an_invalid_argument() -> None:
    device = FakeV4l2System().open(R3S_USB3_LINK)
    assert device.query_control(ids.CID_PAN_ABSOLUTE) is None
    with pytest.raises(OSError) as refused:
        device.get_control(ids.CID_PAN_ABSOLUTE)
    assert refused.value.errno == errno.EINVAL


def test_run_as_a_program_it_prints_its_cameras_and_modes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main()
    printed = capsys.readouterr().out
    assert R3S_USB3_LINK in printed
    assert "mjpeg 3840x2160 @ 30 fps" in printed
