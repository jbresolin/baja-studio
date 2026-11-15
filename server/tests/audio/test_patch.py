"""Tests for the patch the default macros and links follow (audio/patch.py, audio/defaults.py).

The 2026-11-14 bench: the studio's XR18 has SHOTGUN on 3, HOST 1-2 and GUEST
1-2 on 5-8, PROD 1-2 on 9-10, CAMERA 1 on 16 and PC AUDIO on the stereo
return. The defaults were written for the 2026 patch (seats on 1-4, PROD on
5-8), so "Prod 1 → Hosts" and "Prod 1 → Cam" held HOST 1's sends to buses
1, 2 and 6 closed, and its fader moved nothing there. "Restore defaults…"
now builds the macros and the link matrix for the patch in Audio setup:
the seats' microphones, the production microphones, and the other inputs
shown on the audio page.
"""

from typing import Any

from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.patch import patch_of

IN_EARS = ("bus1", "bus2", "bus3", "bus4")


def studio_pc_settings() -> AudioSettings:
    """Audio setup as on the studio PC, once Seats and Production microphones are filled in."""
    shown = {"3", "5", "6", "7", "8", "9", "10", "16", "aux"}
    channels: dict[str, Any] = {
        channel: {"visible": channel in shown} for channel in AudioSettings().channels
    }
    return AudioSettings.model_validate(
        {
            "channels": channels,
            "seats": [
                {"seat_id": 1, "channel": "5", "iem_bus": "bus1"},
                {"seat_id": 2, "channel": "6", "iem_bus": "bus2"},
                {"seat_id": 3, "channel": "7", "iem_bus": "bus3"},
                {"seat_id": 4, "channel": "8", "iem_bus": "bus4"},
            ],
            "production_channels": ["9", "10"],
        }
    )


def macro(macros: list[dict[str, Any]], macro_id: str) -> dict[str, Any]:
    [found] = [m for m in macros if m["id"] == macro_id]
    return found


def points(steps: list[dict[str, Any]]) -> set[str]:
    """Every "channel:bus" a list of steps stores or restores."""
    return {
        f"{point['channel']}:{point['bus']}"
        for step in steps
        if step["op"] in ("store_levels", "restore_levels")
        for point in step["points"]
    }


# --- what the patch is -------------------------------------------------------------------------


def test_a_fresh_install_has_the_2026_patch() -> None:
    # No seat has a microphone yet: channels 1-4 stand in, so a fresh
    # install's defaults are exactly 2026's.
    assert patch_of(AudioSettings()) == defaults.PATCH_2026


def test_the_studio_pc_patch_comes_from_seats_production_and_the_inputs_shown() -> None:
    patch = patch_of(studio_pc_settings())

    assert patch.seats == ((1, "5", "bus1"), (2, "6", "bus2"), (3, "7", "bus3"), (4, "8", "bus4"))
    assert patch.production == ("9", "10")
    # Shown on the audio page and neither a seat's nor a producer's microphone.
    assert patch.others == ("3", "16", "aux")


def test_a_channel_that_is_a_seats_microphone_is_not_also_a_production_microphone() -> None:
    # Production microphones still default to 5-8 when someone maps the seats
    # to 5-8 and forgets to change them: the seats win.
    settings = studio_pc_settings().model_copy(update={"production_channels": ["5", "9"]})

    assert patch_of(settings).production == ("9",)


# --- the link matrix for the studio PC ---------------------------------------------------------


def test_the_hosts_and_guests_follow_main_into_every_in_ear_production_and_the_camera_op() -> None:
    links = defaults.links(patch_of(studio_pc_settings()))

    for channel in ("5", "6", "7", "8"):
        assert links[channel] == dict.fromkeys((*IN_EARS, "bus5", "bus6"), "linked"), channel


def test_the_producers_are_never_in_the_in_ears_and_keep_their_own_level_for_production() -> None:
    links = defaults.links(patch_of(studio_pc_settings()))

    for channel in ("9", "10"):
        assert links[channel] == {"bus5": "paused", "bus6": "paused"}, channel


def test_the_other_main_inputs_follow_main_into_the_in_ears_and_production_but_pc_audio() -> None:
    links = defaults.links(patch_of(studio_pc_settings()))

    for channel in ("3", "16"):  # SHOTGUN, CAMERA 1
        assert links[channel] == dict.fromkeys((*IN_EARS, "bus5"), "linked"), channel
    # PC AUDIO (clip sound) goes to the in-ears, not to production (as in 2026).
    assert links["aux"] == dict.fromkeys(IN_EARS, "linked")
    # Inputs nobody uses are not linked anywhere.
    assert "1" not in links and "2" not in links


# --- the macros for the studio PC --------------------------------------------------------------


def test_talk_to_production_acts_on_each_seats_own_microphone() -> None:
    macros = defaults.macros(patch_of(studio_pc_settings()))

    host1 = macro(macros, "host1-talk-prod")
    # HOST 1 is channel 5: its LR assign goes off, and its sends to the
    # other seats' in-ears (2-4) close; its own in-ears (bus 1) stay.
    assert host1["on_steps"][0] == {"op": "set_lr_assign", "channel": "5", "on": False}
    assert points(host1["on_steps"]) == {"5:bus2", "5:bus3", "5:bus4"}
    assert points(macro(macros, "guest2-talk-prod")["on_steps"]) == {"8:bus1", "8:bus2", "8:bus3"}


def test_the_producers_macros_act_on_the_production_microphones() -> None:
    macros = defaults.macros(patch_of(studio_pc_settings()))

    # Prod 1 → Host 1 and → Host 2: one host's in-ears each.
    assert points(macro(macros, "prod1-talk-hosts")["on_steps"]) == {"9:bus1"}
    assert points(macro(macros, "prod1-talk-host2")["on_steps"]) == {"9:bus2"}
    assert macro(macros, "prod1-talk-host2")["label"] == "Prod 1 → Host 2"
    assert points(macro(macros, "macro-1774582749617")["on_steps"]) == {"9:bus6"}  # Prod 1 → Cam
    assert points(macro(macros, "macro-1774582340101")["on_steps"]) == {"10:main"}  # Prod 2 live
    # Two production microphones: no PROD 3 macros.
    assert not [m for m in macros if m["id"].startswith("prod3")]


def test_no_macro_holds_a_hosts_sends_to_the_hosts_in_ears_or_the_camera_op() -> None:
    # The bench symptom: HOST 1's sends to buses 1, 2 and 6 held by the
    # producers' macros. Now only talk-to-production closes a seat's sends,
    # and never to its own in-ears or to buses 5-6.
    macros = defaults.macros(patch_of(studio_pc_settings()))
    held = set().union(*(points(m["on_steps"]) | points(m["off_steps"]) for m in macros))

    assert not held & {"5:bus1", "5:bus5", "5:bus6", "6:bus2", "6:bus5", "6:bus6"}


def test_the_2026_patch_gives_the_2026_macros_with_their_ids() -> None:
    # 2026's ids kept (2026's "→ Hosts" is now "→ Host 1"), plus "→ Host 2"
    # for each producer (2026-11-14).
    assert [m["id"] for m in defaults.macros()] == [
        "host1-talk-prod",
        "host2-talk-prod",
        "guest1-talk-prod",
        "guest2-talk-prod",
        "prod1-talk-hosts",
        "macro-1774581643177",
        "prod3-talk-hosts",
        "prod1-talk-host2",
        "prod2-talk-host2",
        "prod3-talk-host2",
        "macro-1774582227201",
        "macro-1774582340101",
        "prod3-go-live",
        "macro-1774582749617",
        "macro-1774582877605",
        "prod3-talk-cam",
    ]
