"""Tests for the rig's settings bundle (rig/bundle.py).

The bundle is a settings file in the export format that points every module
at the fakes. Its main danger is drift: a setting renamed in a module while
the rig still writes the old name. So the first tests check that every key
the rig writes is a real setting today, and that every section validates
with this checkout's own models. If one of them fails after a settings
change, update `section_values` in rig/bundle.py.
"""

from pathlib import Path

from baja_studio.core.settings_export import FILE_FORMAT
from baja_studio.core.settings_import import parse_settings_file
from baja_studio.playout.config import PlayoutSettings
from baja_studio.rig import fakes
from baja_studio.rig.bundle import (
    SECTION_MODELS,
    build_bundle,
    section_values,
    unknown_keys,
    without_keys,
)

# Made-up ports: the bundle only writes them down.
PORTS = fakes.FakePorts(
    obs=4455, xr18=10024, cameras=(52381, 52382), agents=(8890, 8891), orchestrator=8000
)
CLIPS = Path("/rig/clips")


def test_every_key_the_rig_writes_is_a_setting_of_this_studio() -> None:
    # The values as the rig wants them, before anything is filtered out.
    for section, values in section_values(PORTS, CLIPS).items():
        # Each section must belong to a module this studio has ...
        assert section in SECTION_MODELS
        # ... and name only fields its settings model (and nested models) has.
        assert unknown_keys(SECTION_MODELS[section], values, section) == []


def test_every_section_validates_with_the_studios_own_models() -> None:
    # The settings models refuse values of the wrong type or shape; a section
    # that does not validate would be skipped by the import.
    for section, values in section_values(PORTS, CLIPS).items():
        SECTION_MODELS[section].model_validate(values)


def test_the_bundle_is_a_settings_file_the_import_accepts(tmp_path: Path) -> None:
    # Build the bundle, write it, and read it back as the import does.
    bundle = build_bundle(PORTS, CLIPS)
    path = tmp_path / "rig-settings.json"
    bundle.write(path)
    parsed = parse_settings_file(path.read_bytes())

    # Nothing was left out, and it is in the export format with every section.
    assert bundle.warnings == []
    assert bundle.document["format"] == FILE_FORMAT
    assert set(parsed.modules) == set(SECTION_MODELS)
    # Secrets are the fakes' own values, so nothing needs typing after the import.
    assert parsed.secrets_required == []


def test_the_bundle_points_every_module_at_the_fakes() -> None:
    values = section_values(PORTS, CLIPS)

    playout = PlayoutSettings.model_validate(values["playout"])
    assert (playout.obs.host, playout.obs.port) == ("127.0.0.1", 4455)
    assert playout.obs.password == fakes.OBS_PASSWORD
    assert playout.sources.orchestrator.base_url == "http://127.0.0.1:8000"
    receiver = playout.sources.receiver_root()
    assert receiver is not None and Path(receiver.path) == CLIPS / "delivery"
    assert [folder.label for folder in playout.sources.local_folders] == [
        "Entregas",
        "Patrocinadores",
    ]

    assert values["audio"]["mixer"] == {"host": "127.0.0.1", "port": 10024, "enabled": True}
    assert [agent["port"] for agent in values["agents"]["agents"]] == [8890, 8891]
    # Each agent's first camera falls back to its own fake VISCA camera.
    fallbacks = [
        camera["direct_fallback"]["address"]["port"]
        for camera in values["cameras"]["cameras"]
        if "direct_fallback" in camera
    ]
    assert fallbacks == [52381, 52382]
    assert values["gateway"] == {"enabled": False}


def test_channels_the_default_links_use_are_visible() -> None:
    # The default link matrix links PROD 3 and 4 (channels 7 and 8); hidden,
    # they would make the readiness run warn "links on hidden channels".
    channels = section_values(PORTS, CLIPS)["audio"]["channels"]
    assert channels["7"]["visible"] and channels["8"]["visible"]


def test_seats_have_names_colours_microphones_and_in_ears() -> None:
    values = section_values(PORTS, CLIPS)
    hosts_seats = values["hosts"]["seats"]
    assert [seat["screen_name"] for seat in hosts_seats] == ["Ana", "Bruno", "Carla", "Diego"]
    assert all(seat["enabled"] and seat["color"].startswith("#") for seat in hosts_seats)
    audio_seats = values["audio"]["seats"]
    assert [(seat["channel"], seat["iem_bus"]) for seat in audio_seats] == [
        ("1", "bus1"),
        ("2", "bus2"),
        ("3", "bus3"),
        ("4", "bus4"),
    ]
    assert values["audio"]["production_bus"] == "bus5"


def test_unknown_keys_are_found_at_any_depth_and_left_out() -> None:
    # A playout section with a misspelt top-level key and a misspelt OBS key.
    values = {"obs": {"host": "127.0.0.1", "pasword": "x"}, "colour": "red"}
    unknown = unknown_keys(PlayoutSettings, values, "playout")
    assert sorted(unknown) == ["playout.colour", "playout.obs.pasword"]

    # Leaving them out keeps everything else.
    assert without_keys(values, unknown, "playout") == {"obs": {"host": "127.0.0.1"}}
