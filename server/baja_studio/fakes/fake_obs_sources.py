"""Video sources, filters and screenshots in the fake OBS (a mixin of `FakeObs`), for WP6b.

What is modelled, and where it comes from:

- **Crop/Pad filter (`crop_filter`)**, from OBS's `plugins/obs-filters/crop-filter.c`
  (read, not measured): settings `relative` (default true), `left`, `top`,
  `right`, `bottom`, `cx`, `cy` (default 0). Relative: the output is the
  source minus the four edges. Not relative: `left`/`top` are the corner and
  `cx`/`cy` the size. An output of zero or less in either direction is 0x0.
  A disabled filter passes the picture through.
- **A source's size is the size after its filters**: `GetSourceScreenshot`
  without a width is that big (obs-websocket's `TakeSourceScreenshot` uses
  `obs_source_get_width`, read from its source). With `imageWidth` only, the
  height follows the aspect ratio; widths outside 8..4096 are refused, and a
  0x0 source cannot be rendered.
- **Events** (obs-websocket 5.x protocol document): changing a filter's
  settings sends `SourceFilterSettingsChanged` (5.4+, also for a client's own
  change), creating one `SourceFilterCreated`, removing one
  `SourceFilterRemoved`, enabling or disabling `SourceFilterEnableStateChanged`,
  and an input whose settings changed (a camera switched to another
  resolution) `InputSettingsChanged` (5.4+).
- **Error codes and words** follow obs-websocket's `RequestStatus` (600
  not found, 601 already exists, 607 bad filter kind, 402 out of range,
  702 processing failed).

Every screenshot is a grey checkerboard PNG drawn over the *whole* source
(so a crop shows which part of the board it holds), whatever format was
asked for: the fake has no JPEG encoder, so its data URI says `image/png`.
Real OBS encodes the format asked for. Screenshots of media inputs and
scenes, which have no size here, are a 1x1 PNG.
"""

import base64
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_protocol import EventSubscription, RequestStatus
from baja_studio.fakes.fake_obs_requests import FakeInput, FakeRequestRefusedError

CROP_FILTER_KIND = "crop_filter"
# Filter kinds this fake OBS has (a real one has many more; these are enough).
KNOWN_FILTER_KINDS = frozenset({CROP_FILTER_KIND, "color_filter", "sharpness_filter"})
CROP_DEFAULTS: dict[str, Any] = {
    "relative": True,
    "left": 0,
    "top": 0,
    "right": 0,
    "bottom": 0,
    "cx": 0,
    "cy": 0,
}
SCREENSHOT_WIDTH_RANGE = (8, 4096)
# The checkerboard has this many squares across the source's width.
CHECKER_SQUARES = 16
# A 1x1 grey PNG: what a source with no size of its own (media, scenes) looks like.
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5E"
    "rkJggg=="
)


@dataclass
class FakeFilter:
    """One filter on a source."""

    name: str
    kind: str
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def full_settings(self) -> dict[str, Any]:
        """The settings with the kind's defaults filled in (what OBS reports)."""
        defaults = CROP_DEFAULTS if self.kind == CROP_FILTER_KIND else {}
        return {**defaults, **self.settings}


@dataclass(frozen=True)
class Region:
    """A part of a source in its own pixels: where the picture after the filters comes from."""

    left: float
    top: float
    width: float
    height: float


class FakeObsSources:
    """Video inputs with a size, their filters, the input list and screenshots."""

    inputs: dict[str, FakeInput]
    scenes: dict[str, Any]
    lock: Any
    video_sizes: dict[str, tuple[int, int]]
    filters: dict[str, list[FakeFilter]]

    def emit(self, event_type: str, event_data: dict[str, Any], category: int) -> None:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

    # --- set up by tests and by the fake's command line ---------------------------------

    def add_video_input(
        self, name: str, width: int, height: int, kind: str = "dshow_input"
    ) -> None:
        """A camera input (a video capture device) whose picture is `width` x `height`."""
        with self.lock:
            self.inputs[name] = FakeInput(kind, settings={"resolution": f"{width}x{height}"})
            self.video_sizes[name] = (width, height)
            self.filters.setdefault(name, [])

    def add_crop_filter(self, source_name: str, filter_name: str) -> None:
        """An empty Crop/Pad filter on a source, as if made in OBS before the studio looked."""
        with self.lock:
            self.filters.setdefault(source_name, []).append(
                FakeFilter(filter_name, CROP_FILTER_KIND)
            )

    def change_video_size(self, name: str, width: int, height: int) -> None:
        """The operator switched the camera to another resolution in its properties."""
        with self.lock:
            self.video_sizes[name] = (width, height)
            settings = {**self.inputs[name].settings, "resolution": f"{width}x{height}"}
            self.inputs[name].settings = settings
            self.emit(
                "InputSettingsChanged",
                {"inputName": name, "inputUuid": f"uuid-{name}", "inputSettings": settings},
                EventSubscription.INPUTS,
            )

    def remove_filter(self, source_name: str, filter_name: str) -> None:
        """The operator deleted a filter in OBS."""
        with self.lock:
            self.filters[source_name] = [
                item for item in self.filters.get(source_name, []) if item.name != filter_name
            ]
            self.emit(
                "SourceFilterRemoved",
                {"sourceName": source_name, "filterName": filter_name},
                EventSubscription.FILTERS,
            )

    def set_filter_enabled(self, source_name: str, filter_name: str, enabled: bool) -> None:
        """The operator ticked or unticked a filter's eye in OBS."""
        with self.lock:
            self._filter(source_name, filter_name).enabled = enabled
            self.emit(
                "SourceFilterEnableStateChanged",
                {"sourceName": source_name, "filterName": filter_name, "filterEnabled": enabled},
                EventSubscription.FILTERS,
            )

    def filter_settings(self, source_name: str, filter_name: str) -> dict[str, Any]:
        """A filter's settings with defaults, for tests to check what was applied."""
        with self.lock:
            return self._filter(source_name, filter_name).full_settings()

    def visible_region(self, source_name: str) -> Region:
        """Which part of the source its filters let through (the whole source without any)."""
        with self.lock:
            width, height = self.video_sizes[source_name]
            region = Region(0.0, 0.0, float(width), float(height))
            for item in self.filters.get(source_name, []):
                if item.enabled and item.kind == CROP_FILTER_KIND:
                    region = _cropped(region, item.full_settings())
            return region

    # --- requests -----------------------------------------------------------------------

    def _request_GetInputList(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        kind = data.get("inputKind")
        return {
            "inputs": [
                {
                    "inputName": name,
                    "inputUuid": f"uuid-{name}",
                    "inputKind": source.kind,
                    "unversionedInputKind": source.kind,
                }
                for name, source in self.inputs.items()
                if kind is None or source.kind == kind
            ]
        }

    def _request_GetSourceFilterList(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = self._source_name(data)
        return {
            "filters": [
                {
                    "filterName": item.name,
                    "filterKind": item.kind,
                    "filterIndex": index,
                    "filterEnabled": item.enabled,
                    "filterSettings": item.full_settings(),
                }
                for index, item in enumerate(self.filters.get(source_name, []))
            ]
        }

    def _request_GetSourceFilter(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = self._source_name(data)
        item = self._filter(source_name, str(data.get("filterName", "")))
        return {
            "filterEnabled": item.enabled,
            "filterIndex": self.filters[source_name].index(item),
            "filterKind": item.kind,
            "filterSettings": item.full_settings(),
        }

    def _request_SetSourceFilterSettings(  # noqa: N802
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        source_name = self._source_name(data)
        item = self._filter(source_name, str(data.get("filterName", "")))
        new_settings = data.get("filterSettings")
        if not isinstance(new_settings, dict):
            raise FakeRequestRefusedError(
                RequestStatus.MISSING_REQUEST_FIELD, "Your request is missing filterSettings."
            )
        if data.get("overlay", True):
            item.settings = {**item.settings, **new_settings}
        else:
            item.settings = dict(new_settings)
        self.emit(
            "SourceFilterSettingsChanged",
            {
                "sourceName": source_name,
                "filterName": item.name,
                "filterSettings": item.full_settings(),
            },
            EventSubscription.FILTERS,
        )
        return {}

    def _request_CreateSourceFilter(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = self._source_name(data)
        name, kind = str(data.get("filterName", "")), str(data.get("filterKind", ""))
        if any(item.name == name for item in self.filters.get(source_name, [])):
            raise FakeRequestRefusedError(
                RequestStatus.RESOURCE_ALREADY_EXISTS, "A filter already exists by that name."
            )
        if kind not in KNOWN_FILTER_KINDS:
            raise FakeRequestRefusedError(
                RequestStatus.INVALID_FILTER_KIND,
                "Your specified filter kind is not supported by OBS. "
                "Check that any necessary plugins are loaded.",
            )
        settings = data.get("filterSettings")
        item = FakeFilter(name, kind, dict(settings) if isinstance(settings, dict) else {})
        self.filters.setdefault(source_name, []).append(item)
        self.emit(
            "SourceFilterCreated",
            {
                "sourceName": source_name,
                "filterName": name,
                "filterKind": kind,
                "filterIndex": len(self.filters[source_name]) - 1,
                "filterSettings": item.full_settings(),
                "defaultFilterSettings": dict(CROP_DEFAULTS) if kind == CROP_FILTER_KIND else {},
            },
            EventSubscription.FILTERS,
        )
        return {}

    def _request_GetSourceScreenshot(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = data.get("sourceName")
        width = data.get("imageWidth")
        low, high = SCREENSHOT_WIDTH_RANGE
        if width is not None and not (isinstance(width, int) and low <= width <= high):
            raise FakeRequestRefusedError(
                RequestStatus.REQUEST_FIELD_OUT_OF_RANGE,
                f"The field value of imageWidth is out of range ({low} to {high}).",
            )
        if source_name in self.video_sizes:
            return {"imageData": self._screenshot(str(source_name), width)}
        if source_name not in self.inputs and source_name not in self.scenes:
            raise FakeRequestRefusedError(
                RequestStatus.RESOURCE_NOT_FOUND,
                f"No source was found by the name of `{source_name}`.",
            )
        return {"imageData": f"data:image/png;base64,{TINY_PNG_BASE64}"}

    # --- helpers --------------------------------------------------------------------------

    def _screenshot(self, source_name: str, requested_width: int | None) -> str:
        region = self.visible_region(source_name)
        out_width, out_height = int(region.width), int(region.height)
        if out_width <= 0 or out_height <= 0:
            raise FakeRequestRefusedError(
                RequestStatus.REQUEST_PROCESSING_FAILED, "Failed to render screenshot."
            )
        if requested_width is not None:
            out_height = max(1, int(requested_width * out_height / out_width))
            out_width = requested_width
        source_width = self.video_sizes[source_name][0]
        png = _checkerboard_png(region, out_width, out_height, source_width / CHECKER_SQUARES)
        return "data:image/png;base64," + base64.b64encode(png).decode()

    def _source_name(self, data: dict[str, Any]) -> str:
        name = data.get("sourceName")
        if not isinstance(name, str) or (name not in self.inputs and name not in self.scenes):
            raise FakeRequestRefusedError(
                RequestStatus.RESOURCE_NOT_FOUND, f"No source was found by the name of `{name}`."
            )
        return name

    def _filter(self, source_name: str, filter_name: str) -> FakeFilter:
        for item in self.filters.get(source_name, []):
            if item.name == filter_name:
                return item
        raise FakeRequestRefusedError(
            RequestStatus.RESOURCE_NOT_FOUND,
            f"No filter was found in the source `{source_name}` with the name `{filter_name}`.",
        )


def _cropped(region: Region, settings: dict[str, Any]) -> Region:
    """The part of `region` a Crop/Pad filter with `settings` lets through."""
    if settings.get("relative", True):
        left, top = float(settings.get("left", 0)), float(settings.get("top", 0))
        width = region.width - left - float(settings.get("right", 0))
        height = region.height - top - float(settings.get("bottom", 0))
    else:
        left, top = float(settings.get("left", 0)), float(settings.get("top", 0))
        width, height = float(settings.get("cx", 0)), float(settings.get("cy", 0))
    if width <= 0 or height <= 0:
        return Region(region.left, region.top, 0.0, 0.0)
    return Region(region.left + left, region.top + top, width, height)


def _checkerboard_png(region: Region, width: int, height: int, square: float) -> bytes:
    """A grayscale PNG of `region` of a checkerboard with `square`-pixel squares, scaled."""
    columns = [
        int((region.left + (x + 0.5) * region.width / width) // square) % 2 for x in range(width)
    ]
    rows = {
        parity: b"\x00" + bytes(0xA0 if (column + parity) % 2 else 0x50 for column in columns)
        for parity in (0, 1)
    }
    raw = b"".join(
        rows[int((region.top + (y + 0.5) * region.height / height) // square) % 2]
        for y in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )
