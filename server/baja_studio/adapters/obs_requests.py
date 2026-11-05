"""Typed helpers for the handful of obs-websocket requests the studio uses.

Mixed into `ObsWebsocketClient`; each helper is one request with its fields
named as in the protocol. Times come back in seconds (OBS speaks milliseconds).
"""

from dataclasses import dataclass
from typing import Any

from baja_studio.adapters.obs_protocol import MediaState


@dataclass(frozen=True)
class MediaStatus:
    """What `GetMediaInputStatus` says about a media source."""

    state: str
    cursor_s: float | None
    duration_s: float | None

    @property
    def has_ended(self) -> bool:
        """True once the file played to its end."""
        return self.state == MediaState.ENDED


@dataclass(frozen=True)
class SceneList:
    """The program scene and every scene name, top of the OBS list first."""

    current_program_scene: str
    scene_names: list[str]


class ObsRequestsMixin:
    """The typed requests; relies on `request` from the client it is mixed into."""

    def request(
        self,
        request_type: str,
        request_data: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Implemented by `ObsWebsocketClient`."""
        raise NotImplementedError

    def get_version(self) -> dict[str, Any]:
        """OBS and obs-websocket versions (`obsVersion`, `obsWebSocketVersion`, ...)."""
        return self.request("GetVersion")

    def get_scene_list(self) -> SceneList:
        """All scenes and the current program scene."""
        data = self.request("GetSceneList")
        # OBS lists scenes bottom-up; reverse so the first is the top of the Scenes dock.
        names = [str(scene["sceneName"]) for scene in reversed(data.get("scenes", []))]
        return SceneList(str(data.get("currentProgramSceneName", "")), names)

    def get_current_program_scene(self) -> str:
        """Name of the scene on program right now."""
        return str(self.request("GetCurrentProgramScene").get("currentProgramSceneName", ""))

    def set_current_program_scene(self, scene_name: str) -> None:
        """Put `scene_name` on program (with the transition OBS would use for that scene)."""
        self.request("SetCurrentProgramScene", {"sceneName": scene_name})

    def get_input_settings(self, input_name: str) -> dict[str, Any]:
        """An input's settings (e.g. a media source's `local_file`)."""
        settings = self.request("GetInputSettings", {"inputName": input_name}).get("inputSettings")
        return settings if isinstance(settings, dict) else {}

    def set_input_settings(
        self, input_name: str, settings: dict[str, Any], overlay: bool = True
    ) -> None:
        """Change an input's settings; `overlay` keeps the settings not mentioned."""
        self.request(
            "SetInputSettings",
            {"inputName": input_name, "inputSettings": settings, "overlay": overlay},
        )

    def trigger_media_input_action(self, input_name: str, action: str) -> None:
        """Play, pause, stop or restart a media source (`MediaAction`)."""
        self.request("TriggerMediaInputAction", {"inputName": input_name, "mediaAction": action})

    def set_media_input_cursor(self, input_name: str, cursor_s: float) -> None:
        """Seek a media source to `cursor_s` seconds."""
        self.request(
            "SetMediaInputCursor", {"inputName": input_name, "mediaCursor": round(cursor_s * 1000)}
        )

    def get_media_input_status(self, input_name: str) -> MediaStatus:
        """State, position and length of a media source."""
        data = self.request("GetMediaInputStatus", {"inputName": input_name})
        cursor, duration = data.get("mediaCursor"), data.get("mediaDuration")
        return MediaStatus(
            state=str(data.get("mediaState", MediaState.NONE)),
            cursor_s=cursor / 1000 if isinstance(cursor, int | float) else None,
            duration_s=duration / 1000 if isinstance(duration, int | float) else None,
        )

    def get_scene_item_id(self, scene_name: str, source_name: str) -> int:
        """The id of `source_name` inside `scene_name`, needed to show or hide it."""
        data = self.request("GetSceneItemId", {"sceneName": scene_name, "sourceName": source_name})
        return int(data["sceneItemId"])

    def set_scene_item_enabled(self, scene_name: str, scene_item_id: int, enabled: bool) -> None:
        """Show or hide one item of a scene."""
        self.request(
            "SetSceneItemEnabled",
            {"sceneName": scene_name, "sceneItemId": scene_item_id, "sceneItemEnabled": enabled},
        )

    def set_scene_transition_override(
        self, scene_name: str, transition_name: str | None, duration_ms: int | None = None
    ) -> None:
        """Make transitions *to* `scene_name` use this transition (None clears it)."""
        self.request(
            "SetSceneSceneTransitionOverride",
            {
                "sceneName": scene_name,
                "transitionName": transition_name,
                "transitionDuration": duration_ms,
            },
        )

    def get_source_screenshot(
        self, source_name: str, image_format: str = "jpg", width: int | None = None
    ) -> str:
        """A still of a source as a data URI (`data:image/jpg;base64,...`), for previews."""
        data: dict[str, Any] = {"sourceName": source_name, "imageFormat": image_format}
        if width is not None:
            data["imageWidth"] = width
        return str(self.request("GetSourceScreenshot", data).get("imageData", ""))
