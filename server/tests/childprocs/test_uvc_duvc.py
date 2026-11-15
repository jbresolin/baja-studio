"""Tests for the UVC child's backend (childprocs/uvc_duvc.py) against the fake duvc library.

The backend takes the *library* as an argument, so here it runs on
`fakes/fake_duvc.py`, which copies duvc-ctl's quirks: reads and writes
return different kinds of result, the Pixy reads back what was written
rather than where it points, and two webcams can share a name.
"""

import pytest

from baja_studio.childprocs.uvc_duvc import DeviceMissingError, DuvcBackend, UvcError
from baja_studio.fakes.fake_duvc import (
    PIXY_PATH,
    S600_A_PATH,
    S600_B_PATH,
    S600_NAME,
    CamMode,
    CamProp,
    FakeDuvcLibrary,
    WriteResult,
    studio_devices,
)


def backend_with_studio_devices() -> tuple[DuvcBackend, FakeDuvcLibrary]:
    """The backend over a fake library with the studio's two S600s and its Pixy plugged in."""
    library = FakeDuvcLibrary(studio_devices())
    return DuvcBackend(library), library


def test_a_write_result_has_no_value_just_like_the_real_library() -> None:
    # The quirk that broke pixy_ptz: a write's result can say whether it
    # worked, but asking it for a value raises. The fake copies that, so any
    # code of ours that asks would fail these tests.
    result = WriteResult()

    assert result.is_ok()
    with pytest.raises(AttributeError):
        result.value()  # type: ignore[attr-defined]


def test_devices_with_the_same_name_are_told_apart_by_path_and_numbered() -> None:
    # Two S600s look identical by name; their DirectShow paths differ (one per
    # USB port), and `index` counts the devices that share a name.
    backend, _ = backend_with_studio_devices()

    listed = [(device.name, device.path, device.index) for device in backend.list_devices()]

    assert listed[0] == (S600_NAME, S600_A_PATH, 0)
    assert listed[1] == (S600_NAME, S600_B_PATH, 1)
    assert listed[2][1] == PIXY_PATH and listed[2][2] == 0


def test_a_control_is_read_with_its_range_and_defaults_from_the_device() -> None:
    backend, _ = backend_with_studio_devices()

    exposure = backend.read(S600_A_PATH, "exposure")
    gain = backend.read(S600_A_PATH, "gain")

    # Exposure has an automatic mode and starts on it; gain has none (auto None).
    assert exposure == {
        "value": -6,
        "auto": True,
        "min": -13,
        "max": -1,
        "step": 1,
        "default": -6,
    }
    assert gain["auto"] is None


def test_supported_lists_studio_names_and_only_the_pixy_has_pan_tilt_zoom() -> None:
    backend, _ = backend_with_studio_devices()

    assert "pan" not in backend.supported(S600_A_PATH)
    assert {"pan", "tilt", "zoom", "white_balance"} <= set(backend.supported(PIXY_PATH))


def test_a_write_reaches_the_device_and_is_read_back() -> None:
    backend, library = backend_with_studio_devices()

    backend.write(S600_A_PATH, "exposure", -9, auto=False)

    assert library.writes == [(S600_A_PATH, "Exposure", -9, "Manual")]
    assert backend.read(S600_A_PATH, "exposure")["value"] == -9
    assert backend.read(S600_A_PATH, "exposure")["auto"] is False


def test_out_of_range_values_and_auto_where_there_is_none_are_refused() -> None:
    # The library says only "Failed to set camera property"; the service adds
    # which value. A refusal is not a dead handle: nothing is reopened.
    backend, library = backend_with_studio_devices()
    backend.read(S600_A_PATH, "exposure")
    opened = library.calls["open_camera"]

    with pytest.raises(UvcError, match="Failed to set camera property"):
        backend.write(S600_A_PATH, "exposure", -20, auto=False)
    with pytest.raises(UvcError, match="Failed to set camera property"):
        backend.write(S600_A_PATH, "gain", 0, auto=True)
    assert library.calls["open_camera"] == opened
    assert backend.take_reopened() == []


def test_the_pixy_reads_back_the_last_value_written_not_where_it_really_points() -> None:
    # Someone turns the Pixy by hand (or its AI tracking does): UVC readback
    # does not change. Only what the studio wrote comes back.
    backend, library = backend_with_studio_devices()
    backend.write(PIXY_PATH, "pan", 30, auto=False)

    library.handle_fake("fake_hand_move", {"path": PIXY_PATH, "pan": -100})

    assert backend.read(PIXY_PATH, "pan")["value"] == 30
    assert library.device(PIXY_PATH).real_pan == -100


def test_an_unplugged_device_is_missing_and_works_again_once_replugged() -> None:
    backend, library = backend_with_studio_devices()
    backend.read(S600_B_PATH, "gain")  # opens (and keeps) a handle to the camera

    library.device(S600_B_PATH).plugged = False
    with pytest.raises(UvcError):
        backend.read(S600_B_PATH, "gain")
    assert S600_B_PATH not in [device.path for device in backend.list_devices()]

    # The old handle is stale after a replug; the backend opens a new one.
    library.device(S600_B_PATH).replug()
    assert backend.read(S600_B_PATH, "gain")["value"] == 0


def test_a_path_is_matched_whatever_its_letter_case() -> None:
    # Windows paths are case-insensitive; settings may hold another spelling.
    backend, _ = backend_with_studio_devices()

    assert backend.read(S600_A_PATH.upper(), "contrast")["value"] == 50


def test_unknown_devices_and_controls_are_errors() -> None:
    backend, _ = backend_with_studio_devices()

    with pytest.raises(DeviceMissingError):
        backend.read(r"\\?\usb#nothing", "gain")
    with pytest.raises(UvcError, match="unknown control"):
        backend.read(S600_A_PATH, "smell")
    with pytest.raises(UvcError, match="Failed to get camera property"):
        backend.read(S600_A_PATH, "pan")  # an S600 cannot pan


def test_auto_follows_the_scene_while_on_and_stops_when_set_manual() -> None:
    # The fake's light knob: exposure on auto picks a new value by itself.
    backend, library = backend_with_studio_devices()

    library.set_scene(1.0)  # bright sunlight
    bright = backend.read(S600_A_PATH, "exposure")["value"]
    library.set_scene(0.0)  # dusk
    dark = backend.read(S600_A_PATH, "exposure")["value"]
    assert bright < dark

    backend.write(S600_A_PATH, "exposure", -7, auto=False)
    library.set_scene(1.0)
    assert backend.read(S600_A_PATH, "exposure")["value"] == -7
    assert library.device(S600_A_PATH).controls[CamProp.Exposure].mode == CamMode.Manual


def test_what_never_changes_is_asked_once_not_on_every_read() -> None:
    # A capability scan lists the devices and opens the filter on the real
    # library: far too slow for every read. After the first read of a device,
    # reads cost only the control's own get and get_range.
    backend, library = backend_with_studio_devices()
    backend.list_devices()
    backend.supported(PIXY_PATH)
    backend.read(PIXY_PATH, "pan")
    library.calls.clear()

    backend.supported(PIXY_PATH)
    backend.read(PIXY_PATH, "tilt")
    backend.write(PIXY_PATH, "tilt", 5, auto=False)

    assert dict(library.calls) == {"get": 1, "get_range": 1, "set": 1}


def test_a_quick_replug_kills_the_handle_and_the_backend_opens_it_again() -> None:
    # Unplugged and plugged back in between two device looks: the device
    # list never changed, but DirectShow's old handle is dead. The write is
    # tried again on a fresh handle, and the path is reported as reopened so
    # the service puts the camera's settings back.
    backend, library = backend_with_studio_devices()
    backend.read(S600_A_PATH, "gain")
    device = library.device(S600_A_PATH)
    device.plugged = False
    device.replug()

    backend.write(S600_A_PATH, "gain", 30, auto=False)

    assert backend.read(S600_A_PATH, "gain")["value"] == 30
    assert backend.take_reopened() == [S600_A_PATH]
    assert backend.take_reopened() == []  # reported once


def test_the_probe_notices_a_quick_replug_with_no_write_at_all() -> None:
    # The service's 2 s check touches every open handle with one read.
    backend, library = backend_with_studio_devices()
    backend.supported(S600_B_PATH)
    backend.read(S600_B_PATH, "gain")
    device = library.device(S600_B_PATH)
    device.plugged = False
    device.replug()

    backend.probe()

    assert [path.lower() for path in backend.take_reopened()] == [S600_B_PATH.lower()]
