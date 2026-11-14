"""Tests for the D200 profile Audio setup builds for Ulanzi Studio (audio/d200_profile.py).

The generated file is read back the way Ulanzi Studio reads it (header,
zip, manifests) and compared with the 2026 profile's layout
(`d200_2026_layout.json`, every button of the 2026 export), so the operators
find every button where it was.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.d200_profile import build_profile, d200_base_url, missing_macros
from baja_studio.audio.patch import patch_of
from tests.audio.ulanzi_profile import BASE, Profile

LAYOUT_2026 = json.loads((Path(__file__).parent / "d200_2026_layout.json").read_text("utf-8"))[
    "pages"
]

# The studio PC on 2026-11-14: HOST 1-2 and GUEST 1-2 on 5-8, PROD 1-2 on 9-10.
STUDIO_PC_SEATS = [
    {"seat_id": 1, "channel": "5", "iem_bus": "bus1"},
    {"seat_id": 2, "channel": "6", "iem_bus": "bus2"},
    {"seat_id": 3, "channel": "7", "iem_bus": "bus3"},
    {"seat_id": 4, "channel": "8", "iem_bus": "bus4"},
]


def studio_pc_settings(**changes: Any) -> AudioSettings:
    """The studio PC's seats and production microphones, after Macros → Restore defaults."""
    settings = AudioSettings.model_validate(
        {"seats": STUDIO_PC_SEATS, "production_channels": ["9", "10"]}
    )
    restored = {**settings.model_dump(), "macros": defaults.macros(patch_of(settings))}
    return AudioSettings.model_validate({**restored, **changes})


def test_the_file_is_what_ulanzi_studio_exports_one_profile_folder_with_its_pages() -> None:
    profile = Profile(build_profile(AudioSettings(), BASE))

    assert profile.root.endswith(".ulanziProfile")
    assert profile.manifest["Device"]["Model"] == "D200"
    assert profile.manifest["Version"] == "2.0"
    assert profile.manifest["Name"] == "Baja Studio"
    # It opens on the main menu.
    assert profile.manifest["Pages"]["Current"] == profile.page_ids[0]
    assert profile.zip.read(f"{profile.root}/{profile.manifest['Icon']}").startswith(b"\x89PNG")


def test_every_image_a_button_names_is_in_its_page_folder_and_nothing_else_is() -> None:
    profile = Profile(build_profile(studio_pc_settings(), BASE))

    for page_id, actions in zip(profile.page_ids, profile.actions, strict=True):
        folder = f"{profile.root}/Profiles/{page_id}/"
        named = set()
        for action in actions.values():
            params, view = action["ActionParam"], action["ViewParam"][0]
            named |= {params.get("image_matched"), params.get("image_unmatched"), view["Icon"]}
        named -= {None, ""}
        held = {
            name.removeprefix(folder)
            for name in profile.zip.namelist()
            if name.startswith(folder) and name.endswith(".png")
        }
        assert named == held
        for path in held:
            assert profile.zip.read(folder + path).startswith(b"\x89PNG")


def macro_button(macro_id: str, on: str, off: str) -> dict[str, str]:
    """A macro toggle as `Profile.buttons()` shows it: press, poll, and its two images."""
    return {
        "request": f"/action/macro/{macro_id}",
        "poll": f"/action/macro-state/{macro_id}",
        "field": "active",
        "on": on,
        "off": off,
    }


def test_for_the_2026_patch_every_2026_button_is_where_it_was_with_the_listed_changes() -> None:
    # The changes from the 2026 profile:
    # - HOST 2's talk button showed GUEST 1's images;
    # - the guests got talk buttons (their macros are from WP9b);
    # - VIDEO is called "aux" (the 2026 server's "17", which the service also understands);
    # - production: "talk to HOST 2" in the 4th column, "talk to camera" moved
    #   to the last one (João, 2026-11-14).
    buttons = Profile(build_profile(AudioSettings(), BASE)).buttons()

    expected = json.loads(json.dumps(LAYOUT_2026))
    expected[1]["1_1"]["on"], expected[1]["1_1"]["off"] = "HOST 2 - PRODDED", "HOST 2 - PROD"
    expected[1]["2_1"] = macro_button("guest1-talk-prod", "GUEST 1 - PRODDED", "GUEST 1 - PROD")
    expected[1]["3_1"] = macro_button("guest2-talk-prod", "GUEST 2 - PRODDED", "GUEST 2 - PROD")
    for page in expected[2:6]:
        for key in ("4_0", "4_1"):
            page[key]["request"] = page[key]["request"].replace("/17/", "/aux/")
    production = expected[6]
    production["4_0"], production["4_1"] = production.pop("3_0"), production.pop("3_1")
    production["3_0"] = macro_button("prod1-talk-host2", "PROD 1 - TALKING 2", "PROD 1 - TALK 2")
    production["3_1"] = macro_button("prod2-talk-host2", "PROD 2 - TALKING 2", "PROD 2 - TALK 2")
    assert buttons == expected


def test_on_the_studio_pcs_patch_the_buttons_call_its_channels() -> None:
    menu, hosts, *in_ears, production = Profile(build_profile(studio_pc_settings(), BASE)).buttons()

    assert [hosts[f"{column}_0"]["request"] for column in range(4)] == [
        "/action/mute/5",
        "/action/mute/6",
        "/action/mute/7",
        "/action/mute/8",
    ]
    assert hosts["0_0"]["poll"] == "/action/state/5"
    # HOST 2's in-ear page (bus 2): every seat up and down, and the clip sound.
    assert [in_ears[1][f"{column}_0"]["request"] for column in range(5)] == [
        "/action/nudge-aux/5/bus2/3",
        "/action/nudge-aux/6/bus2/3",
        "/action/nudge-aux/7/bus2/3",
        "/action/nudge-aux/8/bus2/3",
        "/action/nudge-aux/aux/bus2/3",
    ]
    assert in_ears[1]["0_1"]["request"] == "/action/nudge-aux/5/bus2/-3"
    assert [production[f"0_{row}"]["request"] for row in range(2)] == [
        "/action/mute/9",
        "/action/mute/10",
    ]
    assert production["1_0"]["request"] == "/action/macro/macro-1774582227201"
    assert menu == LAYOUT_2026[0]


def test_every_button_calls_the_given_address() -> None:
    base = "https://studio.example.net"
    profile = Profile(build_profile(studio_pc_settings(), base + "/"))

    urls = [
        action["ActionParam"][key]
        for page in profile.actions
        for action in page.values()
        for key in ("request_url", "poll_status_url")
        if action["ActionParam"].get(key)
    ]
    assert urls
    assert all(url.startswith(base + "/action/") for url in urls)


def test_the_production_page_talks_to_each_host_and_then_the_camera() -> None:
    production = Profile(build_profile(studio_pc_settings(), BASE)).buttons()[-1]

    # One row per production microphone: mute, live, → HOST 1, → HOST 2, → camera.
    assert [production[f"{column}_0"]["request"] for column in range(5)] == [
        "/action/mute/9",
        "/action/macro/macro-1774582227201",
        "/action/macro/prod1-talk-hosts",
        "/action/macro/prod1-talk-host2",
        "/action/macro/macro-1774582749617",
    ]
    assert [production[f"{column}_1"]["request"] for column in range(5)] == [
        "/action/mute/10",
        "/action/macro/macro-1774582340101",
        "/action/macro/macro-1774581643177",
        "/action/macro/prod2-talk-host2",
        "/action/macro/macro-1774582877605",
    ]
    assert production["3_1"]["on"] == "PROD 2 - TALKING 2"


def test_every_macro_button_is_there_even_while_its_macro_is_missing() -> None:
    # 2026-11-14: buttons that disappeared because their macros were not
    # restored yet looked like a broken profile. Now they stay, and
    # `missing_macros` tells Audio setup → D200 which macros to restore.
    settings = studio_pc_settings()
    kept = [m for m in settings.macros if m.id not in ("guest1-talk-prod", "macro-1774582340101")]
    without = settings.model_copy(update={"macros": kept})

    hosts, production = (Profile(build_profile(without, BASE)).buttons()[i] for i in (1, -1))

    assert hosts["2_1"]["request"] == "/action/macro/guest1-talk-prod"
    assert production["1_1"]["request"] == "/action/macro/macro-1774582340101"
    assert missing_macros(without) == ["guest1-talk-prod", "macro-1774582340101"]
    assert missing_macros(settings) == []


def test_a_seat_without_a_microphone_gets_no_buttons_and_no_page() -> None:
    seats = [*STUDIO_PC_SEATS[:3], {"seat_id": 4, "channel": None, "iem_bus": "bus4"}]

    menu, hosts, *in_ears, production = Profile(
        build_profile(studio_pc_settings(seats=seats), BASE)
    ).buttons()

    assert len(in_ears) == 3
    assert menu["1_0"] == {"goto": 5}
    assert not {"3_0", "3_1", "4_1"} & set(hosts)
    assert missing_macros(studio_pc_settings(seats=seats)) == []
    assert hosts["2_2"] == {"goto": 4}
    assert all("3_0" not in page for page in in_ears)
    assert production["0_0"]["request"] == "/action/mute/9"


def test_video_buttons_only_while_pc_audio_is_on_the_audio_page() -> None:
    settings = studio_pc_settings()
    channels = {
        **settings.channels,
        "aux": settings.channels["aux"].model_copy(update={"visible": False}),
    }

    in_ears = Profile(
        build_profile(settings.model_copy(update={"channels": channels}), BASE)
    ).buttons()[2:-1]

    assert all("4_0" not in page and "4_1" not in page for page in in_ears)


def test_each_download_is_a_new_profile() -> None:
    first = Profile(build_profile(AudioSettings(), BASE))
    second = Profile(build_profile(AudioSettings(), BASE))

    assert first.root != second.root
    assert not set(first.page_ids) & set(second.page_ids)


@pytest.mark.parametrize(
    ("text", "base"),
    [
        ("http://localhost:8888", "http://localhost:8888"),
        (" http://192.168.1.20:8888/ ", "http://192.168.1.20:8888"),
        ("https://studio.example.net", "https://studio.example.net"),
    ],
)
def test_the_address_is_scheme_host_and_port(text: str, base: str) -> None:
    assert d200_base_url(text) == base


@pytest.mark.parametrize(
    "text",
    [
        "localhost:8888",
        "ftp://localhost",
        "http://",
        "http://localhost:8888/action",
        "http://x/?a=1",
    ],
)
def test_anything_else_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="D200's address"):
        d200_base_url(text)
