"""What an OBS crop camera asks OBS: its Crop/Pad filter, and the size of its source.

**The source's size.** No obs-websocket request returns a source's own size:
a screenshot, like a scene item's `sourceWidth`, is the size *after* the
source's filters (obs-websocket renders it with `obs_source_get_width`).
With the Crop/Pad filter in its "relative" mode the filter's edges are cut
from the source, so the source is the screenshot plus the edges:

    source width  = screenshot width  + left + right
    source height = screenshot height + top  + bottom

(a disabled filter cuts nothing). OBS applies new edges at its next video
frame, not at once, so a measurement is taken only when this camera has
written nothing for `QUIET_BEFORE_MEASURING_SECONDS`, and the filter is read
again after the screenshot: if its edges changed meanwhile the measurement is
thrown away and tried again. One full-size screenshot (about 1920x1080 at the
narrowest window of a 4K camera) is taken on connect, when the filter
appears, and when the source reports new settings (a new resolution).

These are plain functions over the client; `obs_crop.py` calls them from its worker.
"""

from dataclasses import dataclass

from baja_studio.adapters.obs_images import ImageDataError, decode_data_uri, image_size
from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import ObsRequestFailedError
from baja_studio.agents.contract import Status
from baja_studio.cameras.crop_window import PixelCrop

CROP_FILTER_KIND = "crop_filter"
QUIET_BEFORE_MEASURING_SECONDS = 0.25
# A new filter starts with nothing cut (OBS's defaults, written out).
NEW_FILTER_SETTINGS = PixelCrop(0, 0, 0, 0).filter_settings()


class CropUnavailableError(Exception):
    """The camera cannot be moved now; `status` and the message are for the operator."""

    def __init__(self, status: Status, reason: str) -> None:
        """`status` is degraded (OBS is there, the setup is not) or offline."""
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class FilterReading:
    """The Crop/Pad filter as OBS has it now."""

    enabled: bool
    crop: PixelCrop


def read_filter(client: ObsRequestsMixin, source: str, filter_name: str) -> FilterReading:
    """Read the camera's filter; raises `CropUnavailableError` saying what to fix."""
    try:
        info = client.get_source_filter(source, filter_name)
    except ObsRequestFailedError as error:
        if error.code != RequestStatus.RESOURCE_NOT_FOUND:
            raise
        if source not in {item.name for item in client.get_input_list()}:
            raise CropUnavailableError(
                Status.OFFLINE, f"OBS has no source '{source}' (check the name on Camera setup)"
            ) from None
        raise CropUnavailableError(
            Status.DEGRADED,
            f"source '{source}' has no filter '{filter_name}': use Create filter on Camera setup",
        ) from None
    if info.kind != CROP_FILTER_KIND:
        raise CropUnavailableError(
            Status.DEGRADED,
            f"filter '{filter_name}' on '{source}' is a {info.kind}, not a Crop/Pad filter",
        )
    if not info.settings.get("relative", True):
        raise CropUnavailableError(
            Status.DEGRADED,
            f"filter '{filter_name}' has 'Relative' unticked in OBS; tick it so the studio "
            "can move the crop",
        )
    return FilterReading(info.enabled, PixelCrop.from_filter_settings(info.settings))


def measure_source(
    client: ObsRequestsMixin, source: str, filter_name: str, before: FilterReading
) -> tuple[int, int] | None:
    """The source's own size in pixels, or None if the filter changed while measuring.

    `before` is the filter as read just before. Raises `CropUnavailableError`
    when OBS gives the source no picture (a camera that is off or not shown).
    """
    try:
        image = decode_data_uri(client.get_source_screenshot(source, "jpg"))
        width, height = image_size(image.content)
    except ObsRequestFailedError as error:
        raise CropUnavailableError(
            Status.DEGRADED, f"OBS gives '{source}' no picture ({error.comment or error})"
        ) from None
    except ImageDataError as error:
        raise CropUnavailableError(
            Status.DEGRADED, f"cannot read the size of '{source}': {error}"
        ) from None
    if read_filter(client, source, filter_name) != before:
        return None
    if before.enabled:
        width += before.crop.left + before.crop.right
        height += before.crop.top + before.crop.bottom
    if width <= 1 or height <= 1:
        raise CropUnavailableError(Status.DEGRADED, f"OBS gives '{source}' no picture")
    return width, height
