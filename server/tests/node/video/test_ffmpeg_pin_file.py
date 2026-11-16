"""The node's ffmpeg pin file is complete and consistent (installer/ffmpeg-node/pin.json).

The node installer (and CI) download ffmpeg from the URLs in this file and
refuse anything whose SHA-256 differs. These checks catch a pin edited by
hand with one part forgotten. Whether the build has the right features is
checked against the binary itself in `tests/rig/test_node_ffmpeg_pin.py`.
"""

import json
import re
from pathlib import Path
from typing import Any

PIN_FILE = Path(__file__).parents[4] / "installer" / "ffmpeg-node" / "pin.json"
STUDIO_PIN_FILE = Path(__file__).parents[4] / "installer" / "ffmpeg" / "pin.json"


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_it_has_the_studio_pin_s_fields_and_the_linux_build_for_ci() -> None:
    pin = load(PIN_FILE)
    # Same shape as the studio's ffmpeg pin, so the installer reads both alike.
    assert set(load(STUDIO_PIN_FILE)) <= set(pin)
    assert {"linux_url", "linux_sha256", "linux_folder_in_archive", "license_in_zip"} <= set(pin)


def test_both_downloads_are_https_with_a_sha256() -> None:
    pin = load(PIN_FILE)
    for url, digest in ((pin["url"], pin["sha256"]), (pin["linux_url"], pin["linux_sha256"])):
        assert url.startswith("https://github.com/BtbN/FFmpeg-Builds/releases/download/")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_windows_and_linux_are_the_same_version_from_the_same_release() -> None:
    pin = load(PIN_FILE)
    windows_release = pin["url"].split("/")[-2]
    assert windows_release == pin["linux_url"].split("/")[-2]
    # A monthly build (BtbN keeps those; daily ones vanish after about two weeks).
    assert re.fullmatch(r"autobuild-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}", windows_release)
    for key in ("url", "linux_url", "folder_in_zip", "linux_folder_in_archive"):
        assert pin["version"] in pin[key], key
    assert pin["url"].endswith(f"/{pin['folder_in_zip']}.zip")
    assert pin["linux_url"].endswith(f"/{pin['linux_folder_in_archive']}.tar.xz")
    # The GPL build: x264 (the fallback encoder) is GPL.
    assert "-win64-gpl-" in pin["url"] and "-linux64-gpl-" in pin["linux_url"]
