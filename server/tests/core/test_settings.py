"""Tests for the settings store (core/settings.py).

The store keeps each module's settings as a validated Pydantic model, saved
as JSON in SQLite, with a version number that goes up on every save.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from baja_studio.core.database import Database
from baja_studio.core.settings import (
    SECRET_MASK,
    ModuleSettings,
    SettingsConflictError,
    SettingsStore,
    UnknownSettingsSectionError,
)
from tests.conftest import FakeClock


class CameraSettings(ModuleSettings):
    """A small made-up settings model used only by these tests."""

    address: str = "192.168.0.10"
    port: int = Field(default=52381, ge=1, le=65535)
    password: str = Field(default="", json_schema_extra={"secret": True})


@pytest.fixture
def database(tmp_path: Path) -> Database:
    """A fresh SQLite file in a temporary folder."""
    return Database(tmp_path / "settings-test.sqlite3")


@pytest.fixture
def store(database: Database, fake_clock: FakeClock) -> SettingsStore:
    """A settings store with the made-up "camera" section registered."""
    settings_store = SettingsStore(database, fake_clock)
    settings_store.register("camera", CameraSettings)
    return settings_store


def test_a_new_section_starts_with_the_model_defaults_at_version_1(store: SettingsStore) -> None:
    # Nothing was ever saved for "camera", so we expect the defaults from the model.
    record = store.record("camera")

    assert record.version == 1
    assert record.values == CameraSettings()


def test_an_update_is_validated_saved_and_bumps_the_version(store: SettingsStore) -> None:
    # Change only the port; the other fields should keep their current values.
    updated = store.update("camera", {"port": 5000})

    assert isinstance(updated, CameraSettings)
    assert updated.port == 5000
    assert updated.address == "192.168.0.10"
    assert store.record("camera").version == 2


def test_saved_settings_survive_a_restart(database: Database, fake_clock: FakeClock) -> None:
    # Save a value with one store...
    first_store = SettingsStore(database, fake_clock)
    first_store.register("camera", CameraSettings)
    first_store.update("camera", {"address": "10.0.0.5"})

    # ...then build a brand-new store on the same database, as happens when
    # the service restarts. It should load what was saved, not the defaults.
    second_store = SettingsStore(database, fake_clock)
    loaded = second_store.register("camera", CameraSettings)

    assert isinstance(loaded, CameraSettings)
    assert loaded.address == "10.0.0.5"
    assert second_store.record("camera").version == 2


def test_an_invalid_update_is_rejected_and_nothing_changes(store: SettingsStore) -> None:
    # Port 70000 is outside the allowed 1-65535 range declared in the model.
    with pytest.raises(ValidationError):
        store.update("camera", {"port": 70000})

    # The stored value and version are untouched.
    assert store.record("camera").version == 1
    assert store.get("camera") == CameraSettings()


def test_an_update_with_unknown_fields_is_rejected(store: SettingsStore) -> None:
    # ModuleSettings forbids unknown fields, which catches typos like "adress".
    with pytest.raises(ValidationError):
        store.update("camera", {"adress": "10.0.0.5"})


def test_saving_on_top_of_a_stale_version_raises_a_conflict(store: SettingsStore) -> None:
    # Two browsers both loaded version 1. The first one saves: now version 2.
    store.update("camera", {"port": 1111}, expected_version=1)

    # The second browser still thinks it is editing version 1. Its save must
    # be refused, otherwise it would silently undo the first browser's change.
    with pytest.raises(SettingsConflictError) as conflict:
        store.update("camera", {"port": 2222}, expected_version=1)

    assert conflict.value.current_version == 2
    assert isinstance(store.get("camera"), CameraSettings)
    assert store.get("camera").port == 1111  # type: ignore[attr-defined]


def test_subscribers_are_told_about_every_successful_update(store: SettingsStore) -> None:
    # A subscriber is a function the store calls after each save. Here it
    # just records what it was given.
    received: list[CameraSettings] = []
    store.subscribe("camera", lambda values: received.append(values))  # type: ignore[arg-type]

    store.update("camera", {"port": 4000})

    assert len(received) == 1
    assert received[0].port == 4000


def test_a_failing_subscriber_does_not_stop_the_others(store: SettingsStore) -> None:
    # The first subscriber crashes; the second must still be called.
    def broken_subscriber(values: object) -> None:
        raise RuntimeError("boom")

    received: list[object] = []
    store.subscribe("camera", broken_subscriber)
    store.subscribe("camera", received.append)

    store.update("camera", {"port": 4000})

    assert len(received) == 1


def test_secret_fields_are_masked_when_read_for_the_api(store: SettingsStore) -> None:
    store.update("camera", {"password": "hunter2"})

    shown = store.masked_values("camera")

    # The browser never receives the real password, only a mask.
    assert shown["password"] == SECRET_MASK
    assert shown["address"] == "192.168.0.10"


def test_sending_the_mask_back_keeps_the_existing_secret(store: SettingsStore) -> None:
    # The Settings page sends back every field, including the masked password.
    # The mask must be understood as "unchanged", not saved as the new password.
    store.update("camera", {"password": "hunter2"})

    store.update("camera", {"password": SECRET_MASK, "port": 9})

    current = store.get("camera")
    assert isinstance(current, CameraSettings)
    assert current.password == "hunter2"
    assert current.port == 9


class RemoteSettings(BaseModel):
    """One entry of a made-up list of remote machines, each with its own secret."""

    id: str
    token: str = Field(default="", json_schema_extra={"secret": True})


class FleetSettings(ModuleSettings):
    """Made-up settings holding a list of models, like the agents module does."""

    remotes: list[RemoteSettings] = Field(default_factory=list)


@pytest.fixture
def fleet_store(store: SettingsStore) -> SettingsStore:
    """The same store with a "fleet" section holding two remotes with tokens."""
    store.register("fleet", FleetSettings)
    store.update(
        "fleet", {"remotes": [{"id": "a", "token": "token-a"}, {"id": "b", "token": "token-b"}]}
    )
    return store


def test_secrets_inside_a_list_of_models_are_masked_too(fleet_store: SettingsStore) -> None:
    shown = fleet_store.masked_values("fleet")

    assert shown["remotes"] == [
        {"id": "a", "token": SECRET_MASK},
        {"id": "b", "token": SECRET_MASK},
    ]


def test_masked_secrets_in_a_list_stay_with_their_item_even_after_reordering(
    fleet_store: SettingsStore,
) -> None:
    # The page sends the list back in another order, with one item removed
    # and both remaining tokens still masked. Matching by "id" (not by
    # position) means "b" keeps token-b instead of inheriting token-a.
    fleet_store.update(
        "fleet", {"remotes": [{"id": "b", "token": SECRET_MASK}, {"id": "c", "token": "token-c"}]}
    )

    current = fleet_store.get("fleet")
    assert isinstance(current, FleetSettings)
    assert [(remote.id, remote.token) for remote in current.remotes] == [
        ("b", "token-b"),
        ("c", "token-c"),
    ]


def test_a_masked_secret_for_a_new_list_item_is_saved_as_empty_not_as_the_mask(
    fleet_store: SettingsStore,
) -> None:
    fleet_store.update("fleet", {"remotes": [{"id": "new", "token": SECRET_MASK}]})

    current = fleet_store.get("fleet")
    assert isinstance(current, FleetSettings)
    assert current.remotes[0].token == ""


def test_the_schema_marks_secret_fields_for_the_settings_form(store: SettingsStore) -> None:
    schema = store.schema("camera")

    assert schema["properties"]["password"]["secret"] is True
    assert schema["properties"]["port"]["maximum"] == 65535


def test_saved_keys_the_model_no_longer_has_are_dropped(
    database: Database, fake_clock: FakeClock
) -> None:
    # Simulate an older version of the module that had an extra "zoom" field.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO settings VALUES (?, ?, ?, ?)",
            ("camera", 3, json.dumps({"address": "10.1.1.1", "zoom": 4}), "2026-01-01"),
        )

    loaded = SettingsStore(database, fake_clock).register("camera", CameraSettings)

    # The obsolete key is ignored and the rest is kept.
    assert isinstance(loaded, CameraSettings)
    assert loaded.address == "10.1.1.1"


def test_a_saved_document_that_no_longer_validates_falls_back_to_defaults(
    database: Database, fake_clock: FakeClock
) -> None:
    # A port of -1 is invalid. Startup must not crash because of it.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO settings VALUES (?, ?, ?, ?)",
            ("camera", 3, json.dumps({"port": -1}), "2026-01-01"),
        )

    loaded = SettingsStore(database, fake_clock).register("camera", CameraSettings)

    assert loaded == CameraSettings()


def test_asking_for_an_unregistered_section_raises(store: SettingsStore) -> None:
    with pytest.raises(UnknownSettingsSectionError):
        store.get("does-not-exist")
    with pytest.raises(UnknownSettingsSectionError):
        store.update("does-not-exist", {})


def test_every_secret_value_can_be_listed_for_scrubbing(fleet_store: SettingsStore) -> None:
    # The diagnostics bundle replaces these wherever they appear, even in logs.
    fleet_store.update("camera", {"password": "hunter2"})

    assert sorted(fleet_store.all_secret_values()) == ["hunter2", "token-a", "token-b"]
