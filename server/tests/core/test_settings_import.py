"""Tests for settings import (core/settings_import.py).

An exported file is read back in two steps: a dry run that lists what would
be added, removed or changed in each section (and refuses what cannot be
imported), then the apply, which saves through the normal settings path.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from baja_studio.core.settings import SettingsStore
from baja_studio.core.settings_export import EXPORTED_SECRET
from baja_studio.core.settings_import import (
    SettingsFileError,
    apply_import,
    flatten,
    major_version,
    plan_import,
)
from tests.conftest import FakeClock
from tests.core.demo_settings import (
    DemoSettings,
    NewerDemoSettings,
    OtherSettings,
    exported,
    fresh,
    make_store,
    source,
)

__all__ = ["fresh", "source"]  # fixtures: pytest finds them by their names in this module


def demo_plan(plan: Any) -> Any:
    return next(section for section in plan.sections if section.section == "demo")


def test_onto_a_fresh_install_everything_shows_as_added(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    plan = plan_import(exported(source, fake_clock), fresh)
    demo = demo_plan(plan)

    assert demo.status == "changes"
    assert demo.changed == [] and demo.removed == []
    assert {change.key: change.new for change in demo.added} == {
        "label": "Baja 2026",
        "port": 4456,
        "remote.url": "http://obs",
        "devices[laptop-a].id": "laptop-a",
        "devices[laptop-a].host": "10.0.0.5",
        "devices[laptop-a].token": "",
        "devices[laptop-b].id": "laptop-b",
        "devices[laptop-b].host": "10.0.0.6",
        "devices[laptop-b].token": "",
    }
    # The secrets did not travel and this PC has none: they are the ones to enter.
    assert demo.secrets_to_enter == ["demo.remote.password", "demo.devices[laptop-a].token"]
    assert plan.as_state()["needs_partial"] is False


def test_the_dry_run_lists_changed_and_removed_keys_with_old_and_new(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    # The target already has its own set-up: another label, a device the file
    # does not have, laptop-a at another address.
    fresh.update(
        "demo",
        {
            "label": "rehearsal",
            "devices": [{"id": "laptop-a", "host": "10.0.0.9"}, {"id": "spare", "host": "1.1.1.1"}],
        },
    )

    demo = demo_plan(plan_import(exported(source, fake_clock), fresh))

    changed = {change.key: (change.old, change.new) for change in demo.changed}
    assert changed["label"] == ("rehearsal", "Baja 2026")
    assert changed["devices[laptop-a].host"] == ("10.0.0.9", "10.0.0.5")
    assert {change.key for change in demo.removed} == {
        "devices[spare].id",
        "devices[spare].host",
        "devices[spare].token",
    }


def test_importing_a_pc_onto_itself_changes_nothing(
    source: SettingsStore, fake_clock: FakeClock
) -> None:
    # The file's "<secret>" means "keep what this PC has", so nothing differs.
    plan = plan_import(exported(source, fake_clock), source)

    assert [section.status for section in plan.sections] == ["unchanged", "unchanged"]
    assert plan.as_state()["has_changes"] is False


def test_the_dry_run_names_the_secrets_still_to_enter(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    # The fresh PC already knows laptop-a's token, but not the OBS password.
    fresh.update("demo", {"devices": [{"id": "laptop-a", "token": "already-here"}]})

    demo = demo_plan(plan_import(exported(source, fake_clock), fresh))

    assert demo.secrets_to_enter == ["demo.remote.password"]


def test_a_hand_edited_value_of_the_wrong_type_is_rejected_readably(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    document = json.loads(exported(source, fake_clock))
    document["modules"]["demo"]["values"]["port"] = "four"

    plan = plan_import(json.dumps(document).encode(), fresh)
    demo = demo_plan(plan)

    assert demo.status == "invalid"
    assert demo.messages == [
        "port: Input should be a valid integer, unable to parse string as an integer (got 'four')"
    ]
    # The other section is fine; the import as a whole now needs "apply partial".
    assert plan.as_state()["needs_partial"] is True


def test_keys_this_studio_does_not_know_are_ignored_with_a_note(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    document = json.loads(exported(source, fake_clock))
    document["modules"]["demo"]["values"]["retired_option"] = True

    demo = demo_plan(plan_import(json.dumps(document).encode(), fresh))

    assert demo.status == "changes"
    assert demo.messages == ["keys this studio does not know are ignored: retired_option"]


def test_an_unknown_module_is_skipped_with_a_warning_not_a_failure(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    document = json.loads(exported(source, fake_clock))
    document["modules"]["imaging"] = {"settings_version": 1, "values": {"enabled": True}}

    plan = plan_import(json.dumps(document).encode(), fresh)
    imaging = next(section for section in plan.sections if section.section == "imaging")

    assert imaging.status == "unknown"
    assert imaging.messages == ["this studio has no module 'imaging'; it is skipped"]
    assert plan.as_state()["needs_partial"] is False


def test_a_section_from_a_newer_settings_layout_is_refused(
    tmp_path: Path, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    # A newer studio changed the meaning of "demo" (settings_version 2); this
    # one (version 1) cannot know how to read it.
    newer = make_store(tmp_path / "newer", fake_clock, NewerDemoSettings)

    demo = demo_plan(plan_import(exported(newer, fake_clock), fresh))

    assert demo.status == "invalid"
    assert demo.messages[0].startswith(
        "exported with a newer settings layout (2; this studio has 1)"
    )


def test_a_section_from_an_older_settings_layout_is_flagged_but_imported(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    older = make_store(tmp_path / "older", fake_clock)
    newer = make_store(tmp_path / "newer", fake_clock, NewerDemoSettings)
    older.update("demo", {"label": "from the old PC"})

    demo = demo_plan(plan_import(exported(older, fake_clock), newer))

    assert demo.status == "changes"
    assert demo.messages[0].startswith(
        "exported with an older settings layout (1; this studio has 2)"
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfe\x00", "it is not text"),
        (b"{not json", "the JSON is broken at line 1"),
        (b"[]", "this is not a Baja Studio settings file"),
        (b'{"format": "something-else"}', "this is not a Baja Studio settings file"),
        (b'{"format": "baja-studio-settings", "modules": {}}', "does not say which version"),
        (
            b'{"format": "baja-studio-settings", "app_version": "0.1.0", "modules": {"a": 1}}',
            "'modules' part is damaged",
        ),
    ],
)
def test_files_that_are_not_settings_exports_are_refused_whole(
    fresh: SettingsStore, content: bytes, message: str
) -> None:
    with pytest.raises(SettingsFileError, match=message):
        plan_import(content, fresh)


def test_a_file_from_a_newer_major_version_is_refused_whole(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    document = json.loads(exported(source, fake_clock))
    document["app_version"] = "999.0.0"

    with pytest.raises(SettingsFileError, match="upgrade this PC first"):
        plan_import(json.dumps(document).encode(), fresh)


@pytest.mark.parametrize(
    ("version", "major"), [("1.2.3", 1), ("v2.0.0-rc.1", 2), ("0.1.0.dev5", 0), ("dev", None)]
)
def test_the_major_version_is_read_from_any_version_string(version: str, major: int | None) -> None:
    assert major_version(version) == major


def test_the_token_changes_when_the_settings_change_after_the_dry_run(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    content = exported(source, fake_clock)
    first = plan_import(content, fresh)

    assert plan_import(content, fresh).token == first.token
    fresh.update("other", {"colour": "green"})
    assert plan_import(content, fresh).token != first.token


# --- apply -------------------------------------------------------------------------


def test_apply_goes_through_the_settings_path_and_subscribers_hear_it(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    # A module subscribed to its settings must hear the import like any edit,
    # so it applies the new values without a restart.
    heard: list[BaseModel] = []
    fresh.subscribe("demo", heard.append)
    plan = plan_import(exported(source, fake_clock), fresh)

    results = apply_import(plan, fresh, apply_partial=False)

    assert results == [
        {"section": "demo", "result": "applied"},
        {"section": "other", "result": "applied"},
    ]
    assert len(heard) == 1
    assert isinstance(heard[0], DemoSettings) and heard[0].label == "Baja 2026"


def test_the_round_trip_gives_back_the_same_settings_apart_from_secrets(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    apply_import(plan_import(exported(source, fake_clock), fresh), fresh, apply_partial=False)

    imported = fresh.get("demo").model_dump()
    original = source.get("demo").model_dump()
    # Secrets did not travel: those are exactly the ones to enter again.
    original["remote"]["password"] = ""
    original["devices"][0]["token"] = ""
    assert imported == original
    assert fresh.get("other") == source.get("other")


def test_secrets_stay_as_they_are_unless_the_file_carries_a_real_one(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    fresh.update(
        "demo",
        {"remote": {"password": "kept"}, "devices": [{"id": "laptop-a", "token": "old"}]},
    )
    document = json.loads(exported(source, fake_clock))
    # Someone typed the new token into the file by hand.
    document["modules"]["demo"]["values"]["devices"][0]["token"] = "typed-in"

    plan = plan_import(json.dumps(document).encode(), fresh)
    apply_import(plan, fresh, apply_partial=False)

    demo = fresh.get("demo")
    assert isinstance(demo, DemoSettings)
    assert demo.remote.password == "kept"
    assert demo.devices[0].token == "typed-in"
    # The diff said so, without showing either value.
    token_change = next(c for c in demo_plan(plan).changed if c.key == "devices[laptop-a].token")
    assert (token_change.old, token_change.new, token_change.secret) == (
        EXPORTED_SECRET,
        EXPORTED_SECRET,
        True,
    )


def test_an_invalid_section_blocks_the_import_unless_partial_is_ticked(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    document = json.loads(exported(source, fake_clock))
    document["modules"]["demo"]["values"]["port"] = 0
    plan = plan_import(json.dumps(document).encode(), fresh)

    with pytest.raises(SettingsFileError, match="demo cannot be imported"):
        apply_import(plan, fresh, apply_partial=False)
    assert fresh.get("other") == OtherSettings()  # nothing was saved

    results = apply_import(plan, fresh, apply_partial=True)

    assert results[0]["section"] == "demo"
    assert results[0]["result"].startswith("skipped: port: Input should be greater than")
    assert results[1] == {"section": "other", "result": "applied"}
    assert fresh.get("other") == OtherSettings(colour="blue")


def test_an_unchanged_section_is_not_saved_again(
    source: SettingsStore, fake_clock: FakeClock
) -> None:
    version_before = source.record("demo").version

    results = apply_import(plan_import(exported(source, fake_clock), source), source, False)

    assert results == [
        {"section": "demo", "result": "unchanged"},
        {"section": "other", "result": "unchanged"},
    ]
    assert source.record("demo").version == version_before


def test_a_section_saved_by_someone_else_after_the_dry_run_is_skipped(
    source: SettingsStore, fresh: SettingsStore, fake_clock: FakeClock
) -> None:
    plan = plan_import(exported(source, fake_clock), fresh)
    fresh.update("demo", {"label": "edited meanwhile"})

    results = apply_import(plan, fresh, apply_partial=False)

    assert results[0]["result"].startswith("skipped: someone saved this section meanwhile")
    assert fresh.get("demo").model_dump()["label"] == "edited meanwhile"


def test_flattening_names_list_items_by_id_or_position() -> None:
    values = {"a": {"b": 1}, "items": [{"id": "x", "v": 2}, {"v": 3}], "tags": ["p"], "e": []}

    assert flatten(values) == {
        "a.b": 1,
        "items[x].id": "x",
        "items[x].v": 2,
        "items[1].v": 3,
        "tags[0]": "p",
    }
