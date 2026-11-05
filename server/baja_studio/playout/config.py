"""The playout module's settings (brief §3): sources, OBS, tools, operators, hooks, export.

Nothing is taken from an existing OBS profile: scene and source names are
defaults the bench guide (docs/bench/wp5.md) tells the operator to create.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from baja_studio.core.settings import ModuleSettings

PostClipBehaviour = Literal["return", "hold", "fade"]


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
    """A folder whose video files appear in the library."""

    path: str = Field(description="Absolute folder path on the streaming PC.")
    label: str = Field(default="", description="Shown next to clips from this folder.")
    recursive: bool = Field(default=True, description="Also look inside subfolders.")


class SourcesSettings(StrictModel):
    """Clip sources. The first local folder is the delivery folder (the receiver's clip root)."""

    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    local_folders: list[LocalFolder] = Field(default_factory=list)


class ObsSettings(StrictModel):
    """How to reach OBS and which scenes and sources playout drives."""

    host: str = Field(default="", description="OBS machine; empty means not configured yet.")
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = Field(default="", json_schema_extra={"secret": True})
    scene_a: str = Field(default="Playback A", min_length=1)
    scene_b: str = Field(default="Playback B", min_length=1)
    source_a: str = Field(default="baja_program_a", min_length=1)
    source_b: str = Field(default="baja_program_b", min_length=1)
    return_scene: str = Field(
        default="", description="Scene to go back to after a clip (return and fade)."
    )
    post_clip: PostClipBehaviour = Field(
        default="return", description="return: cut back; hold: stay on the last frame; fade."
    )
    fade_ms: int = Field(default=1000, ge=100, le=10000)
    # Transition names are localised in OBS (pt-BR: "Corte", "Desvanecer").
    cut_transition: str = Field(default="Cut", min_length=1)
    fade_transition: str = Field(default="Fade", min_length=1)


class ToolsSettings(StrictModel):
    """ffprobe/ffmpeg, used for durations and thumbnails."""

    ffprobe_path: str = Field(default="ffprobe", min_length=1)
    ffmpeg_path: str = Field(default="ffmpeg", min_length=1)


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


class PlayoutSettings(ModuleSettings):
    """Everything playout is configured with, edited in the browser."""

    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    obs: ObsSettings = Field(default_factory=ObsSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    operators: OperatorsSettings = Field(default_factory=OperatorsSettings)
    hooks: HooksSettings = Field(default_factory=HooksSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
