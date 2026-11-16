"""Tests for the MediaMTX config writer (modules/feeds/mediamtx_config.py).

The config is compared with golden files in `feeds_golden/`, which a
person can read to see exactly what MediaMTX gets. Those same files were
loaded by the real MediaMTX v1.21.3 (the rig test
`tests/rig/test_mediamtx_real.py` loads them again wherever the binary is
available), so a golden file that changes must be checked there too.

To regenerate them after a deliberate change, run from `server/`:

    python -c "from tests.modules.test_feeds_mediamtx_config import write_goldens; write_goldens()"

and read the diff before committing it.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from argon2.low_level import Type, verify_secret

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings
from baja_studio.modules.feeds.mediamtx_config import (
    FINGERPRINT_FIELD,
    config_document,
    config_fingerprint,
    config_path,
    main,
    parse_config,
    recordings_root,
    render_config,
    write_config,
    write_default_config,
)
from baja_studio.modules.feeds.passwords import MEDIAMTX_ARGON2_PREFIX

GOLDEN_DIRECTORY = Path(__file__).parent / "feeds_golden"
# The streaming PC's default data folder; the same text on Windows and Linux.
GOLDEN_DATA = Path("C:/ProgramData/BajaStudio")
# A fixed salt, so the password hash in the golden file does not change.
SALT = bytes(range(16))
LAPTOP_PASSWORD = "laptop1-pw"
GOLDEN_SETTINGS = {
    # A fresh install: no feeds yet.
    "default": FeedsSettings(),
    # Race day: the drone, a laptop with a password, a laptop being recorded.
    "race": FeedsSettings(
        feeds=[
            Feed(id="feed-drone", name="drone", label="Drone", kind=FeedKind.DRONE),
            Feed(
                id="feed-laptop1",
                name="laptop1",
                label="Laptop 1",
                kind=FeedKind.LAPTOP,
                password=LAPTOP_PASSWORD,
            ),
            Feed(id="feed-laptop2", name="laptop2", kind=FeedKind.LAPTOP, record=True),
        ]
    ),
    # The bench: any name accepted, RTSP open to the LAN, other ports.
    "bench": FeedsSettings(
        feeds=[Feed(id="feed-drone", name="drone")],
        accept_any_name=True,
        rtsp_on_lan=True,
        rtmp_port=1936,
        srt_port=8891,
        rtsp_port=8555,
        api_port=9995,
        recordings_folder="D:/Gravações",
    ),
}


def write_goldens() -> None:
    """Rewrite every golden file from `GOLDEN_SETTINGS` (by hand, after a deliberate change)."""
    for name, settings in GOLDEN_SETTINGS.items():
        text = render_config(settings, GOLDEN_DATA, SALT)
        (GOLDEN_DIRECTORY / f"{name}.yml").write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.parametrize("name", sorted(GOLDEN_SETTINGS))
def test_the_config_matches_its_golden_file(name: str) -> None:
    # The whole file, character for character. If this fails after a
    # deliberate change, regenerate (see the top of this file) and read the diff.
    expected = (GOLDEN_DIRECTORY / f"{name}.yml").read_text(encoding="utf-8")
    assert render_config(GOLDEN_SETTINGS[name], GOLDEN_DATA, SALT) == expected


def document(settings: FeedsSettings) -> dict[str, Any]:
    return config_document(settings, GOLDEN_DATA, SALT)


def test_every_server_the_studio_does_not_use_is_off() -> None:
    # HLS's default port is 8888, the studio's own: it must never be on. The
    # others are off so MediaMTX opens no port nobody needs.
    config = document(FeedsSettings())
    for server in ("hls", "moq", "playback", "metrics", "pprof"):
        assert config[server] is False, server
    # RTSP over TCP only: no RTP/RTCP UDP ports (8000/8001) are opened.
    assert config["rtspTransports"] == ["tcp"]
    assert config["rtspEncryption"] == "no"
    assert config["rtmpEncryption"] == "no"


def test_webrtc_is_on_for_obs_browser_sources_and_only_this_pc_can_ask_for_a_stream() -> None:
    # WebRTC is what an OBS Browser Source reads with the least delay. Its
    # page and its "signalling" (the HTTP request that starts a stream) only
    # answer on 127.0.0.1, so only programs on this PC can start one.
    config = document(FeedsSettings())
    assert config["webrtc"] is True
    assert config["webrtcAddress"] == "127.0.0.1:8889"
    # The browser's page comes from that same address; no other web page may call in.
    assert config["webrtcAllowOrigins"] == ["http://127.0.0.1:8889"]
    # The picture itself goes over UDP. The browser connects to this PC's own
    # addresses (or 127.0.0.1), never through a server on the internet: no
    # STUN or TURN servers, no TCP fallback.
    assert config["webrtcLocalUDPAddress"] == ":8189"
    assert config["webrtcLocalTCPAddress"] == ""
    assert config["webrtcIPsFromInterfaces"] is True
    assert config["webrtcAdditionalHosts"] == ["127.0.0.1"]
    assert config["webrtcICEServers2"] == []


def test_the_webrtc_ports_come_from_the_settings() -> None:
    config = document(FeedsSettings(webrtc_port=2004, webrtc_media_port=2005))
    assert config["webrtcAddress"] == "127.0.0.1:2004"
    assert config["webrtcLocalUDPAddress"] == ":2005"


def test_the_ports_come_from_the_settings_and_the_api_stays_on_loopback() -> None:
    config = document(FeedsSettings(rtmp_port=2000, srt_port=2001, rtsp_port=2002, api_port=2003))
    assert config["rtmpAddress"] == ":2000"
    assert config["srtAddress"] == ":2001"
    assert config["rtspAddress"] == "127.0.0.1:2002"
    assert config["apiAddress"] == "127.0.0.1:2003"


def test_rtsp_and_reading_are_local_unless_opened_to_the_lan() -> None:
    # By default only this PC (OBS) may read a feed: RTSP listens on 127.0.0.1
    # and the "read" permission is for loopback addresses only.
    local = document(FeedsSettings())
    readers = [user for user in local["authInternalUsers"] if _actions(user) == ["read"]]
    assert local["rtspAddress"] == "127.0.0.1:8554"
    assert readers[0]["ips"] == ["127.0.0.1", "::1"]
    # With the setting on, RTSP listens everywhere and anyone may read.
    lan = document(FeedsSettings(rtsp_on_lan=True))
    readers = [user for user in lan["authInternalUsers"] if _actions(user) == ["read"]]
    assert lan["rtspAddress"] == ":8554"
    assert readers[0]["ips"] == []


def _actions(user: dict[str, Any]) -> list[Any]:
    return [permission["action"] for permission in user["permissions"]]


def test_only_listed_feeds_are_paths_and_only_they_may_be_published() -> None:
    settings = GOLDEN_SETTINGS["race"]
    config = document(settings)
    assert sorted(config["paths"]) == ["drone", "laptop1", "laptop2"]
    anonymous = [user for user in config["authInternalUsers"] if user["user"] == "any"]
    publish_paths = [
        permission["path"]
        for user in anonymous
        for permission in user["permissions"]
        if permission["action"] == "publish"
    ]
    # laptop1 has a password, so it is not in the anonymous list.
    assert publish_paths == ["drone", "laptop2"]


def test_a_feed_password_is_written_as_an_argon2_hash_that_mediamtx_can_check() -> None:
    config = document(GOLDEN_SETTINGS["race"])
    laptop1 = next(user for user in config["authInternalUsers"] if user["user"] == "laptop1")
    assert laptop1["permissions"] == [{"action": "publish", "path": "laptop1"}]
    stored = str(laptop1["pass"])
    assert stored.startswith(MEDIAMTX_ARGON2_PREFIX + "$argon2id$")
    # The hash accepts the password and nothing else (what MediaMTX will do).
    encoded = stored.removeprefix(MEDIAMTX_ARGON2_PREFIX).encode()
    assert verify_secret(encoded, LAPTOP_PASSWORD.encode(), Type.ID)
    # And the password itself is nowhere in the file.
    assert LAPTOP_PASSWORD not in render_config(GOLDEN_SETTINGS["race"], GOLDEN_DATA, SALT)


def test_accept_any_name_adds_the_catch_all_path_and_lets_anyone_publish() -> None:
    config = document(GOLDEN_SETTINGS["bench"])
    assert config["paths"] == {"drone": {"record": False}, "all_others": {}}
    anonymous_publish = next(
        user
        for user in config["authInternalUsers"]
        if user["user"] == "any" and _actions(user) == ["publish"]
    )
    # An empty path means "every path" in MediaMTX.
    assert anonymous_publish["permissions"] == [{"action": "publish", "path": ""}]


def test_recordings_go_to_one_folder_per_feed_and_are_never_deleted() -> None:
    # MediaMTX deletes recordings after a day by default: the race's footage must stay.
    defaults = document(GOLDEN_SETTINGS["race"])["pathDefaults"]
    assert defaults["recordPath"] == (
        "C:/ProgramData/BajaStudio/feeds-recordings/%path/%Y-%m-%d_%H-%M-%S-%f_%z"
    )
    assert defaults["recordDeleteAfter"] == "0s"
    assert defaults["recordFormat"] == "fmp4"
    assert document(GOLDEN_SETTINGS["race"])["paths"]["laptop2"] == {"record": True}


def test_the_recordings_folder_setting_replaces_the_default() -> None:
    assert recordings_root(FeedsSettings(), GOLDEN_DATA) == GOLDEN_DATA / "feeds-recordings"
    assert recordings_root(FeedsSettings(recordings_folder="E:/Rec"), GOLDEN_DATA) == Path("E:/Rec")


def test_the_fingerprint_follows_every_change_and_reads_back_from_the_file() -> None:
    # MediaMTX's API reports `sysLogPrefix`; the studio puts a hash of the
    # rest of the file there, to see whether MediaMTX runs the latest file.
    one = document(FeedsSettings(feeds=[Feed(id="a", name="drone")]))
    two = document(FeedsSettings(feeds=[Feed(id="a", name="drone", record=True)]))
    assert one[FINGERPRINT_FIELD] != two[FINGERPRINT_FIELD]
    assert one[FINGERPRINT_FIELD] == config_fingerprint(one)
    text = render_config(FeedsSettings(), GOLDEN_DATA, SALT)
    assert parse_config(text)[FINGERPRINT_FIELD] == document(FeedsSettings())[FINGERPRINT_FIELD]


def test_the_order_of_feeds_in_the_settings_does_not_change_the_file() -> None:
    # Reordering cards on the page must not make MediaMTX reload.
    a, b = Feed(id="a", name="drone"), Feed(id="b", name="laptop1")
    assert render_config(FeedsSettings(feeds=[a, b]), GOLDEN_DATA, SALT) == render_config(
        FeedsSettings(feeds=[b, a]), GOLDEN_DATA, SALT
    )


def test_the_file_is_json_after_its_comment_lines() -> None:
    text = render_config(FeedsSettings(), GOLDEN_DATA, SALT)
    assert text.startswith("# Written by Baja Studio")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    assert json.loads(body)["api"] is True


def test_writing_is_skipped_when_the_text_is_the_same_unless_forced(tmp_path: Path) -> None:
    path = tmp_path / "mediamtx" / "mediamtx.yml"
    assert write_config(path, "one") is True  # the folder is created
    assert write_config(path, "one") is False  # nothing to do: MediaMTX is not disturbed
    # Forced: a new file replaces the old one even with the same text, which
    # MediaMTX's watcher notices (the controller uses it when a reload was missed).
    assert write_config(path, "one", force=True) is True
    assert path.read_text(encoding="utf-8") == "one"
    # The temporary file used for the atomic replace is gone.
    assert not path.with_suffix(".tmp").exists()


def test_the_installer_default_is_written_once_and_upgrades_keep_the_file(tmp_path: Path) -> None:
    # The installer runs this before the feeds service first starts.
    path = write_default_config(tmp_path, only_if_missing=True)
    assert path == config_path(tmp_path)
    assert parse_config(path.read_text(encoding="utf-8"))["paths"] == {}
    # An upgrade keeps the file the studio wrote from the operator's settings.
    path.write_text("# the studio's own\n{}", encoding="utf-8")
    write_default_config(tmp_path, only_if_missing=True)
    assert path.read_text(encoding="utf-8") == "# the studio's own\n{}"


def test_the_command_line_writes_the_default_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["mediamtx_config", "--data-dir", str(tmp_path), "--if-missing"]
    )
    main()
    assert capsys.readouterr().out.strip() == str(config_path(tmp_path))
    assert config_path(tmp_path).is_file()


def test_a_file_windows_will_not_replace_yet_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Windows, replacing a file that another program has open fails
    # ("being used by another process"). MediaMTX opens mediamtx.yml for the
    # moment of a reload, so the writer tries a few times before giving up.
    real_replace = os.replace
    refusals = [PermissionError("in use"), PermissionError("in use")]

    def replace_after_two_refusals(source: Path, target: Path) -> None:
        if refusals:
            raise refusals.pop()
        real_replace(source, target)

    monkeypatch.setattr(
        "baja_studio.modules.feeds.mediamtx_config.os.replace", replace_after_two_refusals
    )
    monkeypatch.setattr(
        "baja_studio.modules.feeds.mediamtx_config.time.sleep", lambda seconds: None
    )
    assert write_config(tmp_path / "mediamtx.yml", "text") is True
    assert (tmp_path / "mediamtx.yml").read_text(encoding="utf-8") == "text"
