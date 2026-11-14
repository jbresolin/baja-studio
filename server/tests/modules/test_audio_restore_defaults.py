"""Tests for Audio setup → "Restore defaults…" (modules/audio/restore_commands.py).

The page previews, shows what changes, and saves through its usual draft:
the macros with the settings save (409 when stale), the link matrix with
`restore_defaults`. Both end up in the command log under the page's client.
"""

from typing import Any

import pytest

from baja_studio.adapters.xr18_addresses import send_address
from baja_studio.audio import defaults
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.restore_commands import default_macros
from tests.modules.audio_studio import AudioStudio

CUSTOM = {
    "id": "jingle-duck",
    "label": "Duck for the jingle",
    "color": "#123456",
    "on_steps": [{"op": "set_fader", "channel": "aux", "level": 0.5}],
    "off_steps": [{"op": "set_fader", "channel": "aux", "level": 0.75}],
}
# Host 1 → Prod as an install from before WP9b has it: it closes the fader.
OLD_HOST1 = {
    "id": "host1-talk-prod",
    "label": "Host 1 → Prod",
    "color": "#e74c3c",
    "on_steps": [{"op": "store_levels", "points": [{"channel": "1", "bus": "main"}]}],
    "off_steps": [{"op": "restore_levels", "points": [{"channel": "1", "bus": "main"}]}],
}


def saved_audio(audio: AudioStudio) -> dict[str, Any]:
    response = audio.http.get("/api/settings/audio")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def old_install(audio: AudioStudio) -> list[dict[str, Any]]:
    """An install with a custom macro, the old Host 1 → Prod, and no guest macros."""
    unchanged = [
        macro.model_dump(mode="json")
        for macro in default_macros()
        if macro.id not in ("host1-talk-prod", "guest1-talk-prod", "guest2-talk-prod")
    ]
    macros = [OLD_HOST1, CUSTOM, *unchanged]
    audio.configure(macros=macros)
    return macros


def test_default_id_macros_are_replaced_missing_ones_added_custom_ones_kept(
    audio: AudioStudio,
) -> None:
    macros = old_install(audio)

    preview = audio.command("preview_defaults", part="macros", macros=macros)

    changes = {change["id"]: change["change"] for change in preview["changes"]}
    assert changes["host1-talk-prod"] == "replaced"
    assert changes["jingle-duck"] == "custom"
    assert changes["guest1-talk-prod"] == changes["guest2-talk-prod"] == "added"
    assert changes["host2-talk-prod"] == changes["prod1-talk-hosts"] == "unchanged"
    by_id = {macro["id"]: macro for macro in preview["macros"]}
    defaults_by_id = {macro.id: macro.model_dump(mode="json") for macro in default_macros()}
    assert by_id["host1-talk-prod"] == defaults_by_id["host1-talk-prod"]
    assert by_id["guest1-talk-prod"] == defaults_by_id["guest1-talk-prod"]
    # The custom macro is left alone, in its place.
    assert preview["macros"][1] == CUSTOM
    assert set(by_id) == set(defaults_by_id) | {"jingle-duck"}


def test_saving_the_restored_macros_and_logging_it_under_the_client(audio: AudioStudio) -> None:
    macros = old_install(audio)
    document = saved_audio(audio)
    preview = audio.command("preview_defaults", part="macros", macros=macros)

    response = audio.http.put(
        "/api/settings/audio",
        json={
            "version": document["version"],
            "values": {**document["values"], "macros": preview["macros"]},
        },
    )
    assert response.status_code == 200
    result = audio.command(
        "restore_defaults",
        source="web:bench-pc",
        part="macros",
        version=response.json()["version"],
        changes=preview["changes"],
    )

    saved = {macro.id: macro for macro in audio.module.settings().macros}
    assert saved["jingle-duck"].label == "Duck for the jingle"
    assert all(saved[macro.id] == macro for macro in default_macros())
    assert result["version"] == response.json()["version"]
    log = audio.log()
    assert ("web:bench-pc", "restore_defaults", "ok") in log
    # The preview changes nothing and is not logged.
    assert all(command != "preview_defaults" for _, command, _ in log)


def test_a_preview_changes_nothing_so_discarding_leaves_everything_as_it_was(
    audio: AudioStudio,
) -> None:
    macros = old_install(audio)
    audio.command("set_link", channel="12", bus="bus1", state="linked")
    before = saved_audio(audio)
    links_before = audio.state("links")

    audio.command("preview_defaults", part="macros", macros=macros)
    audio.command("preview_defaults", part="links")

    assert saved_audio(audio) == before
    assert audio.state("links") == links_before


def test_a_stale_save_of_restored_macros_is_still_refused_with_409(audio: AudioStudio) -> None:
    macros = old_install(audio)
    document = saved_audio(audio)
    preview = audio.command("preview_defaults", part="macros", macros=macros)
    # Someone else saves while the restore waits in this page's draft.
    audio.configure(talkback_bus="bus6")

    response = audio.http.put(
        "/api/settings/audio",
        json={
            "version": document["version"],
            "values": {**document["values"], "macros": preview["macros"]},
        },
    )

    assert response.status_code == 409
    assert audio.module.settings().macro("host1-talk-prod") is not None
    assert audio.module.settings().macro("guest1-talk-prod") is None


def test_logging_a_macro_restore_that_was_not_saved_is_refused(audio: AudioStudio) -> None:
    old_install(audio)

    with pytest.raises(CommandError, match="does not carry the default macros"):
        audio.command("restore_defaults", part="macros", version=saved_audio(audio)["version"])


def test_restoring_the_link_matrix_makes_it_the_seed_and_logs_the_client(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()
    # Before WP9b the production microphones were linked into the in-ears.
    audio.command("set_link", channel="5", bus="bus1", state="linked")
    audio.command("set_link", channel="aux", bus="bus2", state="unlinked")

    preview = audio.command("preview_defaults", part="links")

    assert {(c["channel"], c["bus"], c["before"], c["after"]) for c in preview["changes"]} == {
        ("5", "bus1", "linked", "unlinked"),
        ("aux", "bus2", "unlinked", "linked"),
    }
    result = audio.command(
        "restore_defaults", source="web:bench-pc", part="links", expected=preview["current"]
    )
    audio.settle()

    assert audio.state("links") == defaults.LINKS
    assert len(result["changes"]) == 2
    assert ("web:bench-pc", "restore_defaults", "ok") in audio.log()


def test_restoring_a_link_matrix_changed_since_the_preview_is_refused(audio: AudioStudio) -> None:
    preview = audio.command("preview_defaults", part="links")
    audio.command("set_link", channel="12", bus="bus1", state="linked")

    with pytest.raises(CommandError, match="changed since"):
        audio.command("restore_defaults", part="links", expected=preview["current"])

    assert audio.state("links")["12"] == {"bus1": "linked"}


def test_part_must_be_macros_or_links(audio: AudioStudio) -> None:
    with pytest.raises(CommandError, match="part must be"):
        audio.command("preview_defaults", part="scenes")


# --- the studio PC's patch (2026-11-14 bench) ---------------------------------------------------
#
# HOST 1-2 and GUEST 1-2 on channels 5-8, PROD 1-2 on 9-10. The 2026 macros
# took 5-8 for production microphones: "Prod 1 → Hosts" and "Prod 1 → Cam",
# off, held HOST 1's (channel 5) sends to buses 1, 2 and 6 closed, so its
# fader moved nothing there.

STUDIO_PC_SEATS = [
    {"seat_id": 1, "channel": "5", "iem_bus": "bus1"},
    {"seat_id": 2, "channel": "6", "iem_bus": "bus2"},
    {"seat_id": 3, "channel": "7", "iem_bus": "bus3"},
    {"seat_id": 4, "channel": "8", "iem_bus": "bus4"},
]


def studio_pc(audio: AudioStudio) -> None:
    """Seats and production microphones as on the studio PC, with HOST 1's sends held closed."""
    audio.connect_mixer()
    audio.configure(seats=STUDIO_PC_SEATS, production_channels=["9", "10"])
    # What the old macros left: HOST 1's sends to buses 1, 2 and 6 held (stored, silent).
    for bus in ("bus1", "bus2", "bus6"):
        audio.module.controller.stored.store(f"5:{bus}", 0.75)
    audio.command("set_link", channel="5", bus="bus1", state="linked")


def restore_macros(audio: AudioStudio) -> dict[str, Any]:
    """Press Restore defaults… for the macros, Save, and log it, as the page does."""
    document = saved_audio(audio)
    preview = audio.command("preview_defaults", part="macros", macros=document["values"]["macros"])
    response = audio.http.put(
        "/api/settings/audio",
        json={
            "version": document["version"],
            "values": {**document["values"], "macros": preview["macros"]},
        },
    )
    assert response.status_code == 200
    result: dict[str, Any] = audio.command(
        "restore_defaults",
        part="macros",
        version=response.json()["version"],
        changes=preview["changes"],
    )
    return {"preview": preview, "result": result}


def test_restored_macros_follow_the_seats_and_production_microphones(audio: AudioStudio) -> None:
    studio_pc(audio)

    restore_macros(audio)

    host1 = audio.module.settings().macro("host1-talk-prod")
    prod1_to_hosts = audio.module.settings().macro("prod1-talk-hosts")
    assert host1 is not None and prod1_to_hosts is not None
    assert host1.on_steps[0].model_dump()["channel"] == "5"
    assert prod1_to_hosts.on_steps[0].model_dump()["points"][0]["channel"] == "9"


def test_restoring_releases_the_levels_old_macros_held_and_the_fader_moves_the_sends_again(
    audio: AudioStudio,
) -> None:
    studio_pc(audio)

    restored = restore_macros(audio)

    # The preview says which held levels go, and the restore lets them go.
    # (5:bus2 too: "Host 1 → Prod" closes it only while it is on, and it is off.)
    assert restored["preview"]["released"] == ["5:bus1", "5:bus2", "5:bus6"]
    assert restored["result"]["released"] == ["5:bus1", "5:bus2", "5:bus6"]
    assert audio.module.controller.stored.all() == {}
    # HOST 1's fader now moves its linked sends again.
    audio.command("set_fader", channel="5", level=0.6)
    audio.settle()
    assert audio.mixer_value(send_address("5", "bus1")) == pytest.approx(0.6)


def test_a_level_a_current_macro_holds_is_not_released(audio: AudioStudio) -> None:
    # PROD 1 (channel 9) into the hosts' in-ears is held closed by "Prod 1 →
    # Hosts" while that macro is off (its off steps close it); 9 is a
    # production microphone anyway: it stays held.
    studio_pc(audio)
    audio.module.controller.stored.store("9:bus1", 0.6)

    restored = restore_macros(audio)

    assert "9:bus1" not in restored["result"]["released"]
    assert audio.module.controller.stored.get("9:bus1") == 0.6


def test_the_preview_uses_the_pages_unsaved_seats(audio: AudioStudio) -> None:
    # João maps the seats on the Seats tab and presses Restore defaults…
    # before saving: the preview follows the draft, not the saved setup.
    document = saved_audio(audio)
    draft = {**document["values"], "seats": STUDIO_PC_SEATS, "production_channels": ["9", "10"]}

    preview = audio.command(
        "preview_defaults", part="macros", macros=draft["macros"], settings=draft
    )
    links = audio.command("preview_defaults", part="links", settings=draft)

    by_id = {macro["id"]: macro for macro in preview["macros"]}
    assert by_id["host1-talk-prod"]["on_steps"][0]["channel"] == "5"
    assert links["links"]["9"] == {"bus5": "paused", "bus6": "paused"}
    assert links["links"]["5"]["bus1"] == "linked"


def test_levels_an_active_macro_holds_are_kept(audio: AudioStudio) -> None:
    # Host 1 is talking to production ("Host 1 → Prod" on): its sends to the
    # other seats' in-ears are held closed on purpose, and stay held.
    studio_pc(audio)
    restore_macros(audio)
    # HOST 1 linked into host 2's in-ears and at unity, so that send is open
    # before the macro closes it.
    audio.command("set_link", channel="5", bus="bus2", state="linked")
    audio.command("set_fader", channel="5", level=0.75)
    audio.settle()
    audio.command("run_macro", macro="host1-talk-prod")
    audio.settle()

    preview = audio.command("preview_defaults", part="links")

    assert preview["released"] == []
    assert audio.module.controller.stored.get("5:bus2") is not None


def test_prod_1_talks_to_host_1_at_its_production_bus_level_the_first_time(
    audio: AudioStudio,
) -> None:
    # João, 2026-11-14: "Prod → Host" opened HOST 1's in-ears to nothing until
    # someone set that send by hand. PROD 1 (channel 9) sits at its own level
    # in the production bus (bus 5); its send to HOST 1's in-ears (bus 1) has
    # never been stored, and is off.
    studio_pc(audio)
    restore_macros(audio)
    # As the restored link matrix has it: a production microphone is not
    # linked into the in-ears (the 2026 matrix linked 9, then the shotgun).
    for bus in ("bus1", "bus2"):
        audio.command("set_link", channel="9", bus=bus, state="unlinked")
    audio.change_on_mixer(send_address("9", "bus5"), 0.6)
    audio.change_on_mixer(send_address("9", "bus1"), 0.0)
    cache = audio.module.controller.cache
    audio.soon(lambda: cache.level("9", "bus5") == pytest.approx(0.6))
    audio.soon(lambda: cache.level("9", "bus1") == 0.0)
    assert audio.module.controller.stored.get("9:bus1") is None

    audio.command("run_macro", macro="prod1-talk-hosts")  # Prod 1 → Host 1
    audio.settle()

    assert audio.mixer_value(send_address("9", "bus1")) == pytest.approx(0.6)
    # Only HOST 1's in-ears: HOST 2's stay closed.
    assert audio.mixer_value(send_address("9", "bus2")) == pytest.approx(0.0)
