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
