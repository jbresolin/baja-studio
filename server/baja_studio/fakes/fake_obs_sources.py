"""Video sources, filters and screenshots in the fake OBS (a mixin of `FakeObs`), for WP6b.

The picture itself (filters applied at video frames, a camera reopening at a
new resolution, the drawing) is `fake_obs_video.py`. Here are the requests
and events, after the obs-websocket 5.x protocol document:

- **A source's size is the size after its filters**: `GetSourceScreenshot`
  without a width is that big (obs-websocket's `TakeSourceScreenshot` uses
  `obs_source_get_width`, read from its source). With `imageWidth` only, the
  height follows the aspect ratio; widths outside 8..4096 are refused.
- **Events**: changing a filter's settings sends `SourceFilterSettingsChanged`
  (5.4+, also for a client's own change), creating one `SourceFilterCreated`,
  removing one `SourceFilterRemoved`, enabling or disabling
  `SourceFilterEnableStateChanged`, and an input whose settings changed (a
  camera switched to another resolution) `InputSettingsChanged` (5.4+).
- **Error codes and words** follow obs-websocket's `RequestStatus` (600
  not found, 601 already exists, 607 bad filter kind, 402 out of range).

Every screenshot is a grey checkerboard PNG drawn over the *whole* source
(so a crop shows which part of the board it holds), whatever format was
asked for: the fake has no JPEG encoder, so its data URI says `image/png`.
Real OBS encodes the format asked for. Screenshots of media inputs and
scenes, which have no size here, are a 1x1 PNG.
"""

import base64
from typing import Any

from baja_studio.adapters.obs_protocol import EventSubscription, RequestStatus
from baja_studio.fakes.fake_obs_requests import FakeInput, FakeRequestRefusedError
from baja_studio.fakes.fake_obs_video import (
    CROP_DEFAULTS,
    CROP_FILTER_KIND,
    REOPEN_SECONDS,
    FakeFilter,
    Region,
    VideoInput,
    checkerboard_png,
    cropped,
)

# Filter kinds this fake OBS has (a real one has many more; these are enough).
# `scale_filter` is "Scaling/Aspect Ratio"; the fake does not draw it.
KNOWN_FILTER_KINDS = frozenset(
    {CROP_FILTER_KIND, "scale_filter", "color_filter", "sharpness_filter"}
)
SCREENSHOT_WIDTH_RANGE = (8, 4096)
# The checkerboard has this many squares across the source's width.
CHECKER_SQUARES = 16
# A 1x1 grey PNG: what a source with no size of its own (media, scenes) looks like.
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5E"
    "rkJggg=="
)


class FakeObsSources:
    """Video inputs with a size, their filters, the input list and screenshots."""

    inputs: dict[str, FakeInput]
    scenes: dict[str, Any]
    lock: Any
    video_inputs: dict[str, VideoInput]
    filters: dict[str, list[FakeFilter]]

    def now(self) -> float:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

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
            self.video_inputs[name] = VideoInput(width, height)
            self.filters.setdefault(name, [])

    def add_crop_filter(self, source_name: str, filter_name: str) -> None:
        """An empty Crop/Pad filter on a source, as if made in OBS before the studio looked."""
        with self.lock:
            self.filters.setdefault(source_name, []).append(
                FakeFilter(filter_name, CROP_FILTER_KIND)
            )

    def change_video_size(
        self, name: str, width: int, height: int, named_in_settings: bool = True
    ) -> None:
        """The operator switched the camera to another resolution in its properties.

        The event comes at once; the picture changes size once the device has
        reopened. `named_in_settings` False is a device left at "Device
        default": its settings name no resolution.
        """
        with self.lock:
            video = self.video_inputs[name]
            video.reopening = (self.now() + REOPEN_SECONDS, width, height)
            settings = dict(self.inputs[name].settings)
            if named_in_settings:
                settings["resolution"] = f"{width}x{height}"
            else:
                settings.pop("resolution", None)
                settings["res_type"] = 0
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

    def edit_filter_by_hand(self, source_name: str, filter_name: str, **settings: Any) -> None:
        """The operator changed a filter's values in OBS's filter dialog."""
        with self.lock:
            self._change_settings(source_name, self._filter(source_name, filter_name), settings)

    def visible_region(self, source_name: str) -> Region:
        """Which part of the source its filters let through now (the whole source without any)."""
        with self.lock:
            now = self.now()
            width, height = self.video_inputs[source_name].size(now)
            region = Region(0.0, 0.0, float(width), float(height))
            for item in self.filters.get(source_name, []):
                in_picture = item.picture_settings(now)
                if item.enabled and item.kind == CROP_FILTER_KIND and in_picture is not None:
                    region = cropped(region, in_picture)
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
        if not data.get("overlay", True):
            item.settings = {}
        self._change_settings(source_name, item, new_settings)
        return {}

    def _change_settings(
        self, source_name: str, item: FakeFilter, new_settings: dict[str, Any]
    ) -> None:
        item.change({**item.settings, **new_settings}, self.now())
        self.emit(
            "SourceFilterSettingsChanged",
            {
                "sourceName": source_name,
                "filterName": item.name,
                "filterSettings": item.full_settings(),
            },
            EventSubscription.FILTERS,
        )

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
        item.changed_at = self.now()  # in the picture from the next frame
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
        if source_name in self.video_inputs:
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
        if requested_width is not None:
            out_height = max(1, int(requested_width * out_height / out_width))
            out_width = requested_width
        source_width = self.video_inputs[source_name].size(self.now())[0]
        png = checkerboard_png(region, out_width, out_height, source_width / CHECKER_SQUARES)
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
