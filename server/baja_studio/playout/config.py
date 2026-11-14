"""The playout module's settings (brief §3): sources, OBS, tools, operators, hooks, export.

Nothing is taken from an existing OBS profile: scene and source names are
defaults the bench guide (docs/bench/wp5.md) tells the operator to create.
"""

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from baja_studio.core.settings import ModuleSettings

PostClipBehaviour = Literal["return", "hold", "fade"]
# Blank (or the bare name) means the installer's copy, else PATH.
TOOL_PATH_HELP = "Leave blank for the bundled copy."
# Shown (state, health, readiness) while OBS has no address or no password.
OBS_NOT_CONFIGURED = "OBS not configured: set its address and password in Playout → Settings"


class StrictModel(BaseModel):
    """Nested settings refuse unknown keys, like `ModuleSettings` itself."""

    model_config = ConfigDict(extra="forbid")


class OrchestratorSettings(StrictModel):
    """Where the pipeline's clip manifest comes from (read-only, `GET /manifest`)."""

    enabled: bool = Field(default=False, description="Pull the clip list from the orchestrator.")
    base_url: str = Field(
        default="", description="e.g. http://100.64.0.1:8080 (the VPS's tailnet listener)."
    )
    token: str = Field(
        default="",
        description="The studio's worker bearer token (issued when the studio enrolled).",
        json_schema_extra={"secret": True},
    )
    poll_interval_s: float = Field(default=10.0, ge=2.0, le=600.0)

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")


class LocalFolder(StrictModel):
    """A folder whose video files appear in the library, as a group named by its label."""

    path: str = Field(min_length=1, description="Absolute folder path on the streaming PC.")
    label: str = Field(default="", description="The library group of this folder's clips.")
    recursive: bool = Field(default=True, description="Also look inside subfolders.")
    receiver_root: bool = Field(
        default=False,
        description="The receiver's clip root: delivered clips land here (at most one folder).",
    )

    @field_validator("path", mode="before")
    @classmethod
    def _strip_path(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class SourcesSettings(StrictModel):
    """Clip sources: the orchestrator's manifest and the local folders."""

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    local_folders: list[LocalFolder] = Field(default_factory=list)

    @field_validator("local_folders", mode="before")
    @classmethod
    def _folders_are_objects(cls, value: Any) -> Any:
        # The 2026-11-08 bench saved ["D:/clips"]; say what shape is wanted.
        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, dict | LocalFolder):
                    raise ValueError(
                        f"local_folders[{index}] must be an object like "
                        '{"path": "D:\\\\clips", "label": "Delivery"}, '
                        f"not {type(item).__name__} {item!r}"
                    )
        return value

    @model_validator(mode="after")
    def _one_receiver_root(self) -> "SourcesSettings":
        roots = [folder.path for folder in self.local_folders if folder.receiver_root]
        if len(roots) > 1:
            raise ValueError(f"only one folder can be the receiver root (got {', '.join(roots)})")
        return self

    def receiver_root(self) -> LocalFolder | None:
        """The folder delivered clips land in: the one marked, else the first (WP5 layout)."""
        for folder in self.local_folders:
            if folder.receiver_root:
                return folder
        return self.local_folders[0] if self.local_folders else None


class ObsSettings(StrictModel):
    """How to reach OBS and which scenes and sources playout drives."""

    host: str = Field(default="", description="OBS machine; empty means not configured yet.")
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = Field(default="", json_schema_extra={"secret": True})
    scene_a: str = Field(default="Playback A", min_length=1)
    scene_b: str = Field(default="Playback B", min_length=1)
    source_a: str = Field(default="baja_program_a", min_length=1)
    source_b: str = Field(default="baja_program_b", min_length=1)
    post_clip: PostClipBehaviour = Field(
        default="return",
        description=(
            "return: cut back; hold: stay on the last frame; fade. Where to return to is "
            "chosen on the playout page (previous scene or a chosen one)."
        ),
    )
    fade_ms: int = Field(default=1000, ge=100, le=10000)
    # Transition names are localised in OBS (pt-BR: "Corte", "Desvanecer").
    cut_transition: str = Field(default="Cut", min_length=1)
    fade_transition: str = Field(default="Fade", min_length=1)
    replay_jump_back_s: float = Field(
        default=3.0,
        ge=0.0,
        le=30.0,
        description=(
            "Slow motion on the clip on air is a replay: 50 % or 25 % jumps back this many "
            "seconds (to the keyframe before) and plays from there; 100 % continues from "
            "about where it is."
        ),
    )

    @property
    def is_configured(self) -> bool:
        """True once both the address and the password are set; until then no connection."""
        return bool(self.host.strip()) and bool(self.password)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, values: Any) -> Any:
        # WP5 kept the return scene here; WP5b moved it to a control on the page
        # (session state). `live_speed_change` (swap/reload) went when slow
        # motion became a replay (LIVESTACK-56). Older saved settings and
        # exports still load.
        retired = ("return_scene", "live_speed_change")
        if isinstance(values, dict) and any(key in values for key in retired):
            values = {key: value for key, value in values.items() if key not in retired}
        return values


class ToolsSettings(StrictModel):
    """ffprobe/ffmpeg, used for durations, frame rates and thumbnails (`playout/tools.py`)."""

    ffprobe_path: str = Field(default="", description=TOOL_PATH_HELP)
    ffmpeg_path: str = Field(default="", description=TOOL_PATH_HELP)

    @field_validator("ffprobe_path", "ffmpeg_path", mode="before")
    @classmethod
    def _strip_path(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class OperatorsSettings(StrictModel):
    """Two-operator safety."""

    require_take_confirmation: bool = Field(
        default=False, description="A take needs a second press within 3 s."
    )


class HooksSettings(StrictModel):
    """Outgoing notifications (BajaOvrLay)."""

    on_air_webhook_url: str | None = Field(
        default=None, description="POSTed {clip, started_at} and {clip, ended_at}."
    )


class ExportSettings(StrictModel):
    """The aired log's reference point."""

    stream_start_marker: datetime | None = Field(
        default=None, description="Set with 'mark stream start'; offsets count from it."
    )


class OnAirAction(StrictModel):
    """A command sent to another module when a clip starts or ends.

    Generic so cameras or overlays can be targeted later; today it is
    `{"module": "audio", "command": "run_macro", "args": {"macro": "<id>"}}`.
    """

    module: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class OnAirActions(StrictModel):
    """What runs when a clip goes to air and when it leaves it."""

    on_clip_start: list[OnAirAction] = Field(default_factory=list)
    on_clip_end: list[OnAirAction] = Field(default_factory=list)


class PlayoutSettings(ModuleSettings):
    """Everything playout is configured with, edited in the browser."""

    # 2: obs.return_scene moved to the page; folders gained receiver_root; on_air_actions.
    # 3: obs.live_speed_change replaced by obs.replay_jump_back_s (LIVESTACK-56).
    settings_version: ClassVar[int] = 3

    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    obs: ObsSettings = Field(default_factory=ObsSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    operators: OperatorsSettings = Field(default_factory=OperatorsSettings)
    hooks: HooksSettings = Field(default_factory=HooksSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    on_air_actions: OnAirActions = Field(default_factory=OnAirActions)
