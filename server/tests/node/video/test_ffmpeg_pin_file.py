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


def test_it_is_an_ffmpeg_whose_nvenc_runs_on_the_xps_gtx_1050_ti() -> None:
    # NVENC in an ffmpeg build only works if the NVIDIA driver supports the
    # NVENC API version the build was compiled against. BtbN builds ffmpeg
    # 8.0/8.1 against API 13.0 (driver 570 or newer) and 9.x against the
    # newest API (13.1 needs driver 610 or newer). The XPS's GTX 1050 Ti is a
    # Pascal card: NVIDIA's driver 580 is the last one for it, so a 9.x
    # build can't use its NVENC ("Required: 13.1 Found: 13.0").
    # To move the pin to 9.x or later: check the headers in BtbN's
    # scripts.d/50-ffnvcodec.sh and the oldest laptop's driver, then change this test.
    version = load(PIN_FILE)["version"]  # e.g. "n8.1.3-27-g8dca1586f4"
    major = int(re.match(r"n(\d+)\.", version).group(1))  # type: ignore[union-attr]
    assert major <= 8, version
