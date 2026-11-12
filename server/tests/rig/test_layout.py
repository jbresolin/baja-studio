"""Tests for the rig's folder layout and port picking (rig/layout.py)."""

import sys
from pathlib import Path

import pytest

from baja_studio.rig.layout import RigLayout, default_rig_directory, free_tcp_port, free_udp_port


def test_the_rig_folder_is_apart_from_the_studios_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Users/joao/AppData/Local")))
    folder = default_rig_directory()
    if sys.platform == "win32":
        assert folder == Path("C:/Users/joao/AppData/Local") / "BajaStudioRig"
    else:
        assert folder == Path.home() / ".local" / "share" / "baja-studio-rig"


def test_reset_wipes_clips_studio_data_and_bundle(tmp_path: Path) -> None:
    layout = RigLayout(tmp_path / "rig")
    layout.create()
    (layout.clips / "a.mp4").write_bytes(b"clip")
    layout.bundle.write_text("{}", encoding="utf-8")

    layout.reset()

    assert not layout.root.exists()
    # Resetting a rig that was never made is fine too.
    layout.reset()


def test_free_ports_are_real_port_numbers() -> None:
    # Asking the system for a free port opens and closes a socket on 127.0.0.1;
    # nothing listens afterwards.
    assert 1024 <= free_tcp_port() <= 65535
    assert 1024 <= free_udp_port() <= 65535
