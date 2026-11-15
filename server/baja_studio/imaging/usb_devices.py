"""USB cameras as the UVC child lists them, and how a camera's saved device is found among them.

A camera's `usb_device` holds the DirectShow device path it was picked with,
plus its name and its index among the devices with that name. The path is
the identity: it is stable per USB port, and it tells two S600s (one name)
apart. A device moved to another port gets a new path and is reported
missing until it is picked again in Camera setup; guessing by name could
silently take over the *other* S600.

Name + index is the fallback only for a device DirectShow gives no path
(some virtual cameras have none): it is then found as the `index`-th device
with that name.
"""

from dataclasses import dataclass
from typing import Any

from baja_studio.imaging.config import UsbDeviceRef

MATCHED_BY_PATH = "path"
MATCHED_BY_NAME = "name"


@dataclass(frozen=True)
class UsbDevice:
    """One USB camera plugged into the studio PC."""

    name: str
    path: str
    index: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "UsbDevice":
        """From the child's `{name, path, index}`."""
        return cls(str(data["name"]), str(data["path"]), int(data.get("index", 0)))

    def as_json(self) -> dict[str, Any]:
        """For the state and command results."""
        return {"name": self.name, "path": self.path, "index": self.index}


@dataclass(frozen=True)
class Located:
    """Where a camera's saved USB device is now: the device and how it was found, or why not."""

    device: UsbDevice | None
    matched_by: str = ""
    problem: str = ""


def locate(ref: UsbDeviceRef, present: list[UsbDevice]) -> Located:
    """Find `ref` among the `present` devices: by path, or by name + index if it has no path."""
    label = ref.name or "the USB camera"
    if ref.path:
        for device in present:
            if device.path.lower() == ref.path.lower():
                return Located(device, MATCHED_BY_PATH)
        return Located(
            None,
            problem=(
                f"{label} is not plugged in at its USB port (moved to another port? "
                "pick it again in Camera setup)"
            ),
        )
    same_name = [device for device in present if device.name == ref.name]
    if ref.index < len(same_name):
        return Located(same_name[ref.index], MATCHED_BY_NAME)
    return Located(None, problem=f"{label} is not plugged in")
