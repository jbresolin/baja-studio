"""Tests for playout's settings model (playout/config.py)."""

import pytest
from pydantic import ValidationError

from baja_studio.core.settings import SECRET_MASK, mask_secrets
from baja_studio.playout.config import OrchestratorSettings, PlayoutSettings


def test_the_defaults_leave_obs_unconfigured_and_name_the_brief_scenes() -> None:
    # A fresh install has no settings saved, so every field takes its default.
    settings = PlayoutSettings()

    # OBS has no host yet: the module must say "not configured" rather than
    # guessing an address. The scene and source names are the brief's.
    assert settings.obs.host == ""
    assert settings.obs.port == 4455
    assert (settings.obs.scene_a, settings.obs.scene_b) == ("Playback A", "Playback B")
    assert (settings.obs.source_a, settings.obs.source_b) == ("baja_program_a", "baja_program_b")
    assert settings.obs.post_clip == "return"
    # Pulling from the orchestrator is opt-in, with the brief's 10 s poll.
    assert settings.sources.orchestrator.enabled is False
    assert settings.sources.orchestrator.poll_interval_s == 10.0
    assert settings.sources.local_folders == []
    assert settings.operators.require_take_confirmation is False
    assert settings.hooks.on_air_webhook_url is None
    assert settings.export.stream_start_marker is None


def test_the_obs_password_and_orchestrator_token_are_masked_at_any_depth() -> None:
    # Both secrets sit inside nested sections; the API masks what it sends out.
    settings = PlayoutSettings.model_validate(
        {
            "obs": {"password": "obs-secret"},
            "sources": {"orchestrator": {"token": "studio-token"}},
        }
    )

    masked = mask_secrets(PlayoutSettings, settings.model_dump(mode="json"))

    # Neither secret may reach a browser; other fields are untouched.
    assert masked["obs"]["password"] == SECRET_MASK
    assert masked["sources"]["orchestrator"]["token"] == SECRET_MASK
    assert masked["obs"]["scene_a"] == "Playback A"


def test_a_trailing_slash_on_the_orchestrator_url_is_dropped() -> None:
    # Operators paste URLs with and without a final slash; requests join paths
    # onto the base, so the slash is removed once, here.
    settings = OrchestratorSettings(base_url=" http://100.64.0.1:8080/ ")

    assert settings.base_url == "http://100.64.0.1:8080"


def test_unknown_keys_in_a_nested_section_are_refused() -> None:
    # A typo ("hostname" instead of "host") must be an error the Settings page
    # shows, not a silently ignored value.
    with pytest.raises(ValidationError):
        PlayoutSettings.model_validate({"obs": {"hostname": "streaming-pc"}})


def test_post_clip_accepts_only_return_hold_or_fade() -> None:
    # The three behaviours the brief lists; anything else is refused.
    with pytest.raises(ValidationError):
        PlayoutSettings.model_validate({"obs": {"post_clip": "loop"}})


def test_a_folder_given_as_a_plain_string_is_refused_naming_the_field() -> None:
    # Regression, bench 2026-11-08: ["D:/clips"] was saved, and the module then
    # said "no delivery folder". The save must fail and say what shape is wanted.
    with pytest.raises(ValidationError) as refused:
        PlayoutSettings.model_validate({"sources": {"local_folders": ["D:/clips"]}})

    message = str(refused.value)
    assert "local_folders[0] must be an object like" in message
    assert "'D:/clips'" in message


def test_a_folder_needs_a_path() -> None:
    with pytest.raises(ValidationError):
        PlayoutSettings.model_validate({"sources": {"local_folders": [{"path": "  "}]}})


def test_the_receiver_root_is_the_marked_folder_else_the_first() -> None:
    marked = PlayoutSettings.model_validate(
        {
            "sources": {
                "local_folders": [
                    {"path": "/sponsors", "label": "Patrocinadores"},
                    {"path": "/clips", "receiver_root": True},
                ]
            }
        }
    )
    unmarked = PlayoutSettings.model_validate(
        {"sources": {"local_folders": [{"path": "/clips"}, {"path": "/sponsors"}]}}
    )

    root = marked.sources.receiver_root()
    assert root is not None and root.path == "/clips"
    # WP5 settings had no flag: the first folder stays the delivery folder.
    root = unmarked.sources.receiver_root()
    assert root is not None and root.path == "/clips"
    assert PlayoutSettings().sources.receiver_root() is None


def test_only_one_folder_can_be_the_receiver_root() -> None:
    with pytest.raises(ValidationError, match="only one folder can be the receiver root"):
        PlayoutSettings.model_validate(
            {
                "sources": {
                    "local_folders": [
                        {"path": "/a", "receiver_root": True},
                        {"path": "/b", "receiver_root": True},
                    ]
                }
            }
        )


def test_settings_saved_before_wp5b_still_load_without_their_return_scene() -> None:
    # The return scene moved to a control on the playout page; an old saved
    # document (or export) still carrying it must load, and drops it.
    settings = PlayoutSettings.model_validate({"obs": {"host": "pc", "return_scene": "Main"}})

    assert settings.obs.host == "pc"
    assert "return_scene" not in settings.obs.model_dump()
    assert PlayoutSettings.settings_version >= 2


def test_settings_saved_with_the_old_speed_change_method_still_load_without_it() -> None:
    # Before LIVESTACK-56 a live speed change was "swap" or "reload"
    # (`obs.live_speed_change`). Slow motion is now a replay that jumps
    # back `obs.replay_jump_back_s`; an old saved document (or export) still
    # carrying the method must load, and drops it. Version 3 says so.
    settings = PlayoutSettings.model_validate(
        {"obs": {"host": "pc", "live_speed_change": "reload"}}
    )

    assert "live_speed_change" not in settings.obs.model_dump()
    assert settings.obs.replay_jump_back_s == 3.0
    assert PlayoutSettings.settings_version == 3


@pytest.mark.parametrize("seconds", [-1.0, 30.5])
def test_the_replay_jump_back_stays_between_0_and_30_seconds(seconds: float) -> None:
    with pytest.raises(ValidationError):
        PlayoutSettings.model_validate({"obs": {"replay_jump_back_s": seconds}})


def test_on_air_actions_default_to_none_and_name_module_command_and_args() -> None:
    assert PlayoutSettings().on_air_actions.on_clip_start == []

    settings = PlayoutSettings.model_validate(
        {
            "on_air_actions": {
                "on_clip_start": [
                    {"module": "audio", "command": "run_macro", "args": {"macro": "mute"}}
                ]
            }
        }
    )

    (action,) = settings.on_air_actions.on_clip_start
    assert (action.module, action.command, action.args) == ("audio", "run_macro", {"macro": "mute"})
