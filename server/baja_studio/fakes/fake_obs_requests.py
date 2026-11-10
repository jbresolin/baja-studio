"""The requests the fake OBS answers, as a mixin of `FakeObs`.

Each `_request_<Name>` takes the request data and returns the response data,
or raises `FakeRequestRefusedError` with the status code real OBS would send.
Only the requests the studio uses are here; others answer "unknown request".
"""

from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_protocol import MediaAction, RequestStatus
from baja_studio.fakes.simulated_media import SimulatedMedia

# A 1x1 grey PNG: enough for a page to show "a screenshot came back".
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5E"
    "rkJggg=="
)


class FakeRequestRefusedError(Exception):
    """A request the fake refuses, with OBS's status code."""

    def __init__(self, code: int, comment: str) -> None:
        """Keep the code for the response's `requestStatus`."""
        super().__init__(comment)
        self.code = code
        self.comment = comment


@dataclass
class FakeSceneItem:
    """A source placed in a scene."""

    scene_item_id: int
    source_name: str
    enabled: bool = True


@dataclass
class FakeInput:
    """An input; media inputs (`ffmpeg_source`) also have playback."""

    kind: str
    settings: dict[str, Any] = field(default_factory=dict)
    media: SimulatedMedia = field(default_factory=SimulatedMedia)
    muted: bool = False


class FakeObsRequests:
    """Request handlers over the fake's scenes and inputs."""

    scenes: dict[str, list[FakeSceneItem]]
    inputs: dict[str, FakeInput]
    program_scene: str
    transitions: list[str]
    transition_overrides: dict[str, tuple[str | None, int | None]]
    stuck_files: set[str]

    def now(self) -> float:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

    def duration_of(self, file: str) -> float | None:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

    def emit(self, event_type: str, event_data: dict[str, Any], category: int) -> None:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

    def change_program_scene(self, scene_name: str) -> None:
        """Implemented by `FakeObs`."""
        raise NotImplementedError

    def _request_GetVersion(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        return {"obsVersion": "32.0.0-fake", "obsWebSocketVersion": "5.6.0", "rpcVersion": 1}

    def _request_GetSceneList(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        # OBS lists scenes bottom-up, with sceneIndex counting from the bottom.
        names = list(reversed(self.scenes))
        return {
            "currentProgramSceneName": self.program_scene,
            "currentPreviewSceneName": None,
            "scenes": [{"sceneName": name, "sceneIndex": i} for i, name in enumerate(names)],
        }

    def _request_GetSceneTransitionList(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        return {
            "currentSceneTransitionName": self.transitions[0] if self.transitions else None,
            "transitions": [
                {"transitionName": name, "transitionKind": "fake", "transitionFixed": False}
                for name in self.transitions
            ],
        }

    def _request_GetCurrentProgramScene(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        return {"currentProgramSceneName": self.program_scene}

    def _request_SetCurrentProgramScene(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self.change_program_scene(self._scene_name(data))
        return {}

    def _request_GetInputSettings(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source = self._input(data)
        return {"inputSettings": dict(source.settings), "inputKind": source.kind}

    def _request_SetInputSettings(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source = self._input(data)
        new_settings = data.get("inputSettings")
        if not isinstance(new_settings, dict):
            raise FakeRequestRefusedError(
                RequestStatus.MISSING_REQUEST_FIELD, "inputSettings missing"
            )
        old_file = source.settings.get("local_file", "")
        old_speed = source.settings.get("speed_percent", 100)
        source.settings = (
            {**source.settings, **new_settings} if data.get("overlay", True) else dict(new_settings)
        )
        new_file = str(source.settings.get("local_file", ""))
        new_speed = source.settings.get("speed_percent", 100)
        source.media.speed = float(new_speed) / 100
        if new_file != old_file:
            # It opens now and starts on the next tick (MediaInputPlaybackStarted then).
            source.media.load(new_file, self.duration_of(new_file), self.now())
            source.media.stuck = new_file in self.stuck_files
        elif new_speed != old_speed:
            # A new speed reopens the file from the top (assumed; bench step 6 checks OBS 32).
            source.media.reload(self.now())
        return {}

    def _request_SetInputMute(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self._input(data).muted = bool(data.get("inputMuted"))
        return {}

    def _request_GetInputMute(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        return {"inputMuted": self._input(data).muted}

    def _request_TriggerMediaInputAction(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        media, now = self._input(data).media, self.now()
        action = data.get("mediaAction")
        if action == MediaAction.PLAY:
            media.play(now)
        elif action == MediaAction.PAUSE:
            media.pause(now)
        elif action == MediaAction.STOP:
            media.stop(now)
        elif action == MediaAction.RESTART:
            media.restart(now)
        else:
            raise FakeRequestRefusedError(
                RequestStatus.MISSING_REQUEST_FIELD, f"bad action {action}"
            )
        return {}

    def _request_SetMediaInputCursor(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        cursor_ms = data.get("mediaCursor")
        if not isinstance(cursor_ms, int | float):
            raise FakeRequestRefusedError(
                RequestStatus.MISSING_REQUEST_FIELD, "mediaCursor missing"
            )
        media = self._input(data).media
        if not media.is_seekable:
            # Real OBS's words (the 2026-11-08 bench saw them on a cue).
            raise FakeRequestRefusedError(
                RequestStatus.INVALID_RESOURCE_STATE,
                "The media input must be playing or paused in order to set the cursor position.",
            )
        media.seek(cursor_ms / 1000, self.now())
        return {}

    def _request_GetMediaInputStatus(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        media = self._input(data).media
        duration = media.duration_s
        has_file = bool(media.file)
        return {
            "mediaState": media.state,
            "mediaDuration": round(duration * 1000) if has_file and duration is not None else None,
            "mediaCursor": round(media.cursor(self.now()) * 1000) if has_file else None,
        }

    def _request_GetSceneItemId(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = data.get("sourceName")
        for item in self.scenes[self._scene_name(data)]:
            if item.source_name == source_name:
                return {"sceneItemId": item.scene_item_id}
        raise FakeRequestRefusedError(
            RequestStatus.RESOURCE_NOT_FOUND, f"no {source_name} in scene"
        )

    def _request_SetSceneItemEnabled(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        for item in self.scenes[self._scene_name(data)]:
            if item.scene_item_id == data.get("sceneItemId"):
                item.enabled = bool(data.get("sceneItemEnabled"))
                return {}
        raise FakeRequestRefusedError(RequestStatus.RESOURCE_NOT_FOUND, "no such scene item")

    def _request_SetSceneSceneTransitionOverride(  # noqa: N802
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        name = self._scene_name(data)
        self.transition_overrides[name] = (
            data.get("transitionName"),
            data.get("transitionDuration"),
        )
        return {}

    def _request_GetSourceScreenshot(self, data: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        source_name = data.get("sourceName")
        if source_name not in self.inputs and source_name not in self.scenes:
            raise FakeRequestRefusedError(
                RequestStatus.RESOURCE_NOT_FOUND, f"no source {source_name}"
            )
        return {"imageData": f"data:image/png;base64,{TINY_PNG_BASE64}"}

    def _scene_name(self, data: dict[str, Any]) -> str:
        name = data.get("sceneName")
        if not isinstance(name, str):
            raise FakeRequestRefusedError(RequestStatus.MISSING_REQUEST_FIELD, "sceneName missing")
        if name not in self.scenes:
            raise FakeRequestRefusedError(RequestStatus.RESOURCE_NOT_FOUND, f"no scene {name!r}")
        return name

    def _input_name(self, data: dict[str, Any]) -> str:
        name = data.get("inputName")
        if not isinstance(name, str):
            raise FakeRequestRefusedError(RequestStatus.MISSING_REQUEST_FIELD, "inputName missing")
        if name not in self.inputs:
            raise FakeRequestRefusedError(RequestStatus.RESOURCE_NOT_FOUND, f"no input {name!r}")
        return name

    def _input(self, data: dict[str, Any]) -> FakeInput:
        return self.inputs[self._input_name(data)]
