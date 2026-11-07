"""A small made-up settings model with secrets at every depth, for the export and import tests.

`source` is a PC that is fully set up (secrets included); `fresh` is one just
installed. Both are real SQLite files in the test's temporary folder.
"""

import json
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field

from baja_studio.core.database import Database
from baja_studio.core.settings import ModuleSettings, SettingsStore
from baja_studio.core.settings_export import export_document
from tests.conftest import FakeClock


class Remote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""
    password: str = Field(default="", json_schema_extra={"secret": True})


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    host: str = ""
    token: str = Field(default="", json_schema_extra={"secret": True})


class DemoSettings(ModuleSettings):
    label: str = "studio"
    port: int = Field(default=4455, ge=1, le=65535)
    remote: Remote = Field(default_factory=Remote)
    devices: list[Device] = Field(default_factory=list)


class OtherSettings(ModuleSettings):
    colour: str = "red"


class NewerDemoSettings(DemoSettings):
    settings_version: ClassVar[int] = 2


def make_store(
    folder: Path, clock: FakeClock, demo: type[DemoSettings] = DemoSettings
) -> SettingsStore:
    """A settings store with two sections, "demo" and "other", in its own database file."""
    folder.mkdir(parents=True, exist_ok=True)
    store = SettingsStore(Database(folder / "studio.sqlite3"), clock)
    store.register("demo", demo)
    store.register("other", OtherSettings)
    return store


@pytest.fixture
def source(tmp_path: Path, fake_clock: FakeClock) -> SettingsStore:
    """The PC we export from, fully configured, with secrets set."""
    store = make_store(tmp_path / "source", fake_clock)
    store.update(
        "demo",
        {
            "label": "Baja 2026",
            "port": 4456,
            "remote": {"url": "http://obs", "password": "obs-password"},
            "devices": [
                {"id": "laptop-a", "host": "10.0.0.5", "token": "token-a"},
                {"id": "laptop-b", "host": "10.0.0.6"},
            ],
        },
    )
    store.update("other", {"colour": "blue"})
    return store


@pytest.fixture
def fresh(tmp_path: Path, fake_clock: FakeClock) -> SettingsStore:
    """A PC just installed: every section at its defaults."""
    return make_store(tmp_path / "fresh", fake_clock)


def exported(store: SettingsStore, clock: FakeClock) -> bytes:
    return json.dumps(export_document(store, clock, "STREAM-PC")).encode("utf-8")
