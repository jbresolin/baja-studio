"""Tests for Audio setup → "Restore defaults…" (modules/audio/restore_commands.py).

The page previews, shows what changes, and saves through its usual draft:
the macros with the settings save (409 when stale), the link matrix with
`restore_defaults`. Both end up in the command log under the page's client.
"""

from typing import Any

import pytest

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
