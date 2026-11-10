"""Tests for audio's links and offsets in the settings file (modules/audio/routing_commands.py).

The Settings → Export / import page saves `export_routing`'s answer in the
settings file and sends it back with `import_routing`. What travels is the
matrix as designed and the seats' offsets, never the pauses of the moment.
"""

from typing import Any

import pytest

from baja_studio.adapters.xr18_addresses import send_address
from baja_studio.adapters.xr18_levels import level_to_db
from baja_studio.core.module import CommandError
from tests.modules.audio_studio import AudioStudio


def test_export_then_import_brings_back_the_links_and_offsets(audio: AudioStudio) -> None:
    audio.connect_mixer()
    # Host 1 hears the shotgun 2 dB up; a knob paused HOST 1's link to bus 1.
    audio.command("set_seat_offset", seat_id=1, channel="9", delta_db=2.0)
    audio.command("set_link", channel="1", bus="bus1", state="paused")
    exported = audio.command("export_routing")
    # The pause is not in the file: bus1 is still one of HOST 1's links.
    assert "bus1" in exported["links"]["1"]
    assert exported["offsets"] == {"9:bus1": 2.0}

    # Things change at the next event...
    audio.command("set_link", channel="4", bus="bus6", state="unlinked")
    audio.command("set_link", channel="12", bus="bus1", state="linked")
    audio.command("reset_seat_offsets", seat_id=1)
    audio.settle()

    # ...and the file puts them back.
    result = audio.command("import_routing", **exported)
    audio.settle()

    assert result["applied"] is True
    assert audio.command("export_routing") == exported
    links = audio.state("links")
    assert links["1"]["bus1"] == "linked"  # imported as linked, not paused
    assert links["4"]["bus6"] == "linked"
    assert "12" not in links
    assert audio.state("offsets") == {"9:bus1": 2.0}
    # The mixer is online, so the shotgun's send is at main + 2 dB at once.
    value = audio.mixer_value(send_address("9", "bus1"))
    assert isinstance(value, float)
    assert level_to_db(value) == pytest.approx(2.0)


def test_a_dry_run_says_what_would_change_and_changes_nothing(audio: AudioStudio) -> None:
    before = audio.command("export_routing")
    wanted: dict[str, Any] = {"links": {"12": ["bus2"]}, "offsets": {"12:bus2": -3}}

    result = audio.command("import_routing", dry_run=True, **wanted)

    assert result["applied"] is False
    assert result["added"] == ["12:bus2"]
    assert "1:bus1" in result["removed"]
    assert result["offsets_changed"] == ["12:bus2"]
    assert audio.command("export_routing") == before


def test_importing_works_with_the_mixer_offline(audio: AudioStudio) -> None:
    # Settings are often imported on a PC before the desk is plugged in.
    result = audio.command("import_routing", links={"9": ["bus1", "bus2"]}, offsets={})

    assert result["applied"] is True
    assert audio.state("links") == {"9": {"bus1": "linked", "bus2": "linked"}}


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"links": [], "offsets": {}}, "'links' must be"),
        ({"links": {"lr": ["bus1"]}, "offsets": {}}, "unknown input 'lr'"),
        ({"links": {"1": ["main"]}, "offsets": {}}, "not bus1-bus6"),
        ({"links": {}, "offsets": []}, "'offsets' must be"),
        ({"links": {}, "offsets": {"1-bus1": 2}}, "not an input:bus"),
        ({"links": {}, "offsets": {"1:bus1": "loud"}}, "is not a number"),
        ({"links": {}, "offsets": {"1:bus1": 20}}, "outside -30…10 dB"),
    ],
)
def test_a_damaged_routing_section_is_refused_and_nothing_changes(
    audio: AudioStudio, args: dict[str, Any], problem: str
) -> None:
    before = audio.command("export_routing")

    with pytest.raises(CommandError, match=problem):
        audio.command("import_routing", **args)
    assert audio.command("export_routing") == before
