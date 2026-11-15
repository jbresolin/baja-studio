"""Tests for finding a camera's saved USB device among those plugged in (imaging/usb_devices.py).

Two S600s share one name. The DirectShow path tells them apart (it is stable
per USB port), so it is what a camera is found by. Name + index is only for
a device that has no path at all.
"""

from baja_studio.imaging.config import UsbDeviceRef
from baja_studio.imaging.usb_devices import UsbDevice, locate

S600 = "EMEET SmartCam S600"
PORT_1, PORT_2, PORT_3 = r"\\?\usb#port1", r"\\?\usb#port2", r"\\?\usb#port3"


def test_the_path_finds_the_device_whatever_its_letter_case() -> None:
    present = [UsbDevice(S600, PORT_1, 0), UsbDevice(S600, PORT_2, 1)]

    located = locate(UsbDeviceRef(path=PORT_2.upper(), name=S600, index=1), present)

    assert located.device == UsbDevice(S600, PORT_2, 1)
    assert located.matched_by == "path"


def test_a_device_on_another_port_is_missing_rather_than_guessed_by_name() -> None:
    # Camera "b" was picked on port 2. Port 2 is empty; another S600 sits on
    # port 3. It might be "b" moved, or the spare: the studio does not guess
    # (driving the wrong camera is worse), and says what to do.
    present = [UsbDevice(S600, PORT_1, 0), UsbDevice(S600, PORT_3, 1)]

    located = locate(UsbDeviceRef(path=PORT_2, name=S600, index=1), present)

    assert located.device is None
    assert located.problem == (
        f"{S600} is not plugged in at its USB port (moved to another port? "
        "pick it again in Camera setup)"
    )


def test_a_device_without_a_path_is_found_by_name_and_index() -> None:
    # Some virtual cameras have no DirectShow path: then the name and the
    # place among the devices with that name are all there is.
    present = [UsbDevice("Virtual Cam", "", 0), UsbDevice("Virtual Cam", "", 1)]

    located = locate(UsbDeviceRef(name="Virtual Cam", index=1), present)

    assert located.device == UsbDevice("Virtual Cam", "", 1)
    assert located.matched_by == "name"
    assert locate(UsbDeviceRef(name="Virtual Cam", index=2), present).problem == (
        "Virtual Cam is not plugged in"
    )
