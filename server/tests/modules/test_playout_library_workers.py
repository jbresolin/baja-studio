"""Tests for playout's library workers (modules/playout/library_workers.py).

The scanner walks a real temporary folder, the prober is the fake ffprobe
from playout_studio.py, and the orchestrator is the fake one behind a mock
HTTP transport. Checks read the library as browsers see it.
"""

from pathlib import Path

from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.fake_orchestrator import SAMPLE_START
from tests.conftest import eventually
from tests.modules.playout_studio import (
    CLIP_SECONDS,
    ORCHESTRATOR_TOKEN,
    ORCHESTRATOR_URL,
    PlayoutStudio,
)


def orchestrator_settings(**overrides: object) -> dict[str, object]:
    return {
        "enabled": True,
        "base_url": ORCHESTRATOR_URL,
        "token": ORCHESTRATOR_TOKEN,
        "poll_interval_s": 2.0,
        **overrides,
    }


def test_dropped_files_appear_with_a_duration_and_a_thumbnail(playout: PlayoutStudio) -> None:
    # Bench step 2, first half: three files in the delivery folder.
    playout.watch_folder()
    for name in ("intro.mp4", "sponsor.mov", "notes.txt"):
        playout.add_file(name)

    clip = playout.wait_for_clip("intro.mp4")
    playout.wait_for_clip("sponsor.mov")

    assert clip["duration_s"] == CLIP_SECONDS
    assert clip["has_thumbnail"] is True
    assert clip["source"] == "manual"
    assert clip["title"] == "intro"
    assert playout.clip("notes.txt") is None  # not a video
    # The thumbnail is cached in the data folder, never next to the clips.
    thumbnails = list((playout.services.data_directory / "playout-thumbnails").iterdir())
    assert len(thumbnails) == 2
    assert not any(path.suffix == ".jpg" for path in playout.clips_folder.iterdir())


def test_a_file_ffprobe_cannot_read_stays_in_the_library_without_a_duration(
    playout: PlayoutStudio,
) -> None:
    playout.prober.failing.add("broken.mp4")
    playout.watch_folder()
    playout.add_file("broken.mp4")

    playout.soon(lambda: (playout.clip("broken.mp4") or {}).get("probe_error") is not None)

    clip = playout.clip("broken.mp4")
    assert clip is not None
    assert clip["duration_s"] is None
    assert clip["queueable"] is True  # OBS may still play it
    # It is not probed again and again, even after several scans...
    playout.pass_time(12.0)
    assert playout.prober.probed.count(str(playout.clips_folder / "broken.mp4")) == 1
    # ...until the tools settings change (a fixed ffprobe path deserves a retry).
    playout.prober.failing.clear()
    playout.configure(tools={"ffprobe_path": "C:/ffmpeg/bin/ffprobe.exe", "ffmpeg_path": "ffmpeg"})
    playout.wait_for_clip("broken.mp4")


def test_an_unreadable_folder_makes_the_module_degraded_and_names_it(
    playout: PlayoutStudio, tmp_path: Path
) -> None:
    playout.connect_obs()
    missing = tmp_path / "unplugged-drive"

    playout.configure(sources={"local_folders": [{"path": str(missing)}]})

    eventually(lambda: str(missing) in playout.state("folders.errors"))
    eventually(
        lambda: playout.services.supervisor.module_health("playout").state == HealthState.DEGRADED
    )


def test_orchestrator_metadata_is_merged_onto_the_matching_delivered_file(
    playout: PlayoutStudio,
) -> None:
    # Bench step 2, second half: a clip with metadata shows prova/car/team.
    playout.orchestrator.deliver("Enduro", 42, "Equipe Cerrado", 3, SAMPLE_START)
    playout.add_file("enduro/42/3.mp4")
    playout.watch_folder()

    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder), "label": "Delivery"}],
            "orchestrator": orchestrator_settings(),
        }
    )

    playout.soon(lambda: (playout.clip("enduro/42/3.mp4") or {}).get("prova") == "Enduro")
    clip = playout.clip("enduro/42/3.mp4")
    assert clip is not None
    assert clip["source"] == "orchestrator"
    assert (clip["car_number"], clip["team"], clip["passagem"]) == (42, "Equipe Cerrado", 3)
    assert clip["title"] == "Enduro · carro 42 [Equipe Cerrado] · passagem 3"
    # The sync status is published just after the merge, so wait for it too.
    playout.soon(lambda: playout.state("sync_status.state") == "ok")


def test_a_clip_announced_before_its_file_arrives_is_shown_missing_then_becomes_playable(
    playout: PlayoutStudio,
) -> None:
    playout.orchestrator.deliver("Suspensão", 7, None, 1, SAMPLE_START)
    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder)}],
            "orchestrator": orchestrator_settings(),
        }
    )
    playout.soon(lambda: playout.clip("suspensao/7/1.mp4") is not None)
    assert playout.clip("suspensao/7/1.mp4")["missing"] is True  # type: ignore[index]

    playout.add_file("suspensao/7/1.mp4")

    clip = playout.wait_for_clip("suspensao/7/1.mp4")
    assert clip["missing"] is False
    assert clip["prova"] == "Suspensão"


def test_an_unreachable_orchestrator_is_degraded_and_the_last_list_stays(
    playout: PlayoutStudio,
) -> None:
    playout.connect_obs()
    playout.orchestrator.deliver("Enduro", 42, None, 3, SAMPLE_START)
    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder)}],
            "orchestrator": orchestrator_settings(),
        }
    )
    playout.soon(lambda: playout.state("sync_status.state") == "ok")
    last_success = playout.state("sync_status.last_success_at")

    playout.orchestrator.available = False

    playout.soon(lambda: playout.state("sync_status.state") == "unreachable")
    assert playout.state("sync_status.last_success_at") == last_success
    assert playout.clip("enduro/42/3.mp4") is not None
    eventually(
        lambda: playout.services.supervisor.module_health("playout").state == HealthState.DEGRADED
    )


def test_a_wrong_token_is_reported_as_such(playout: PlayoutStudio) -> None:
    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder)}],
            "orchestrator": orchestrator_settings(token="not-the-token"),
        }
    )

    playout.soon(lambda: playout.state("sync_status.state") == "unreachable")
    assert "token" in playout.state("sync_status.error")


def test_without_a_delivery_folder_the_sync_says_what_is_missing(playout: PlayoutStudio) -> None:
    playout.configure(sources={"orchestrator": orchestrator_settings()})

    playout.soon(lambda: playout.state("sync_status.state") == "error")
    assert "delivery folder" in playout.state("sync_status.error")


def test_later_pulls_ask_only_for_clips_delivered_since_the_newest_one(
    playout: PlayoutStudio,
) -> None:
    playout.orchestrator.deliver("Enduro", 42, None, 3, SAMPLE_START)
    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder)}],
            "orchestrator": orchestrator_settings(),
        }
    )
    playout.soon(lambda: playout.clip("enduro/42/3.mp4") is not None)

    # The first pull of a run is a full one (no `since`). After one poll
    # interval the next pull asks only for what came after the newest clip
    # seen, minus a minute of overlap (merging the same clip twice is harmless).
    playout.soon(lambda: len(playout.orchestrator_queries) >= 2, within_seconds=3)
    assert "since" not in playout.orchestrator_queries[0]
    assert playout.orchestrator_queries[1]["since"].startswith("2026-11-01T11:59:00")
