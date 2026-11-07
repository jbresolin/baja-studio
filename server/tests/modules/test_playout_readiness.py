"""Tests for playout's readiness checks (modules/playout/readiness.py).

Playout runs with its fakes (tests/modules/playout_studio.py): a fake OBS on
the in-process network, a fake orchestrator, a temporary clips folder.
ffprobe and ffmpeg are looked up on disk by the check, so a test that needs
them "found" points the settings at two empty executable files.
"""

import stat
from pathlib import Path

from baja_studio.core.readiness import CheckStatus
from baja_studio.core.system_probes import GIGABYTE
from tests.core.fake_system_probes import FakeSystemProbes
from tests.modules.playout_studio import (
    OBS_HOST,
    OBS_PORT,
    ORCHESTRATOR_TOKEN,
    ORCHESTRATOR_URL,
    PlayoutStudio,
    ready_studio,
)
from tests.modules.readiness_runner import assert_fix_given, run_module_checks, statuses


def fake_tools(folder: Path) -> dict[str, str]:
    """Two executable files standing in for ffprobe and ffmpeg."""
    paths = {}
    for name in ("ffprobe", "ffmpeg"):
        tool = folder / f"{name}.exe"
        tool.write_bytes(b"")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        paths[f"{name}_path"] = str(tool)
    return paths


def checks(playout: PlayoutStudio) -> dict[str, CheckStatus]:
    return statuses(run_module_checks(playout.services, "playout", playout.clock))


def test_a_ready_studio_passes_every_check(playout: PlayoutStudio, tmp_path: Path) -> None:
    ready_studio(playout)
    playout.configure(tools=fake_tools(tmp_path))

    results = run_module_checks(playout.services, "playout", playout.clock)

    assert statuses(results) == {
        "obs": CheckStatus.OK,
        "obs_scenes": CheckStatus.OK,
        "clip_root": CheckStatus.OK,
        "tools": CheckStatus.OK,
        "orchestrator": CheckStatus.OK,
        "queue_files": CheckStatus.OK,
        "disk:0": CheckStatus.OK,
    }
    assert results["obs_scenes"].detail == "Playback A, Playback B, transition Cut"
    assert results["orchestrator"].detail == "not used (local clips only)"
    assert results["disk:0"].label == "Free space for Delivery"


def test_obs_stopped_is_one_red_line_and_its_scenes_only_a_warning(
    playout: PlayoutStudio,
) -> None:
    # Bench §7 step 2: stopping OBS should show one problem, not four.
    playout.connect_obs()
    playout.network.unplug(OBS_HOST, OBS_PORT)
    playout.soon(lambda: playout.state("obs.connection") != "connected")

    results = run_module_checks(playout.services, "playout", playout.clock)

    assert results["obs"].status == CheckStatus.FAIL
    assert_fix_given(results["obs"])
    assert f"port {OBS_PORT}" in results["obs"].fix
    assert results["obs_scenes"].status == CheckStatus.WARN
    assert results["obs_scenes"].detail.startswith("not checked: OBS is not connected")


def test_obs_not_configured_fails(playout: PlayoutStudio) -> None:
    result = run_module_checks(playout.services, "playout", playout.clock)["obs"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.startswith("not_configured: OBS not configured")


def test_missing_scenes_sources_and_transition_are_named(playout: PlayoutStudio) -> None:
    # OBS is reachable, but the profile on this PC is not the bench one.
    playout.connect_obs(
        scene_b="Playback Z", source_a="wrong_source", return_scene="Gone", cut_transition="Corte"
    )

    result = run_module_checks(playout.services, "playout", playout.clock)["obs_scenes"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == (
        "no scene 'Playback Z'; scene 'Playback A' has no source 'wrong_source'; "
        "no return scene 'Gone'; no transition 'Corte' (OBS has Cut, Fade)"
    )
    assert_fix_given(result)


def test_no_return_scene_with_return_after_a_clip_warns(playout: PlayoutStudio) -> None:
    playout.connect_obs(return_scene="")

    result = run_module_checks(playout.services, "playout", playout.clock)["obs_scenes"]

    assert result.status == CheckStatus.WARN
    assert result.fix == "Set the return scene in Settings → Playout."


def test_no_clip_folder_fails_and_a_missing_one_says_so(
    playout: PlayoutStudio, tmp_path: Path
) -> None:
    unset = run_module_checks(playout.services, "playout", playout.clock)["clip_root"]
    playout.configure(sources={"local_folders": [{"path": str(tmp_path / "unplugged-drive")}]})
    missing = run_module_checks(playout.services, "playout", playout.clock)["clip_root"]

    assert unset.status == CheckStatus.FAIL
    assert unset.detail == "no local folder is set: delivered clips have nowhere to go"
    assert missing.status == CheckStatus.FAIL
    assert missing.detail.endswith("unplugged-drive does not exist")


def test_tools_that_are_not_found_are_named(playout: PlayoutStudio, tmp_path: Path) -> None:
    tools = fake_tools(tmp_path)
    tools["ffmpeg_path"] = str(tmp_path / "nowhere" / "ffmpeg.exe")
    playout.configure(tools=tools)

    result = run_module_checks(playout.services, "playout", playout.clock)["tools"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == f"not found: {tmp_path / 'nowhere' / 'ffmpeg.exe'}"


def test_an_unreachable_orchestrator_only_warns(playout: PlayoutStudio) -> None:
    # The event can run on local clips, so this never makes the studio "not ready".
    orchestrator = {
        "enabled": True,
        "base_url": ORCHESTRATOR_URL,
        "token": ORCHESTRATOR_TOKEN,
        "poll_interval_s": 2.0,
    }
    playout.watch_folder()
    playout.configure(
        sources={
            "local_folders": [{"path": str(playout.clips_folder)}],
            "orchestrator": orchestrator,
        }
    )
    playout.soon(lambda: playout.state("sync_status.state") == "ok")
    reachable = run_module_checks(playout.services, "playout", playout.clock)["orchestrator"]

    playout.orchestrator.available = False
    playout.soon(lambda: playout.state("sync_status.state") == "unreachable")
    unreachable = run_module_checks(playout.services, "playout", playout.clock)["orchestrator"]

    assert reachable.status == CheckStatus.OK
    assert unreachable.status == CheckStatus.WARN
    assert unreachable.detail.startswith("unreachable")
    assert_fix_given(unreachable)


def test_a_queued_clip_whose_file_went_away_fails(playout: PlayoutStudio) -> None:
    first, second = ready_studio(playout, files=("a.mp4", "b.mp4"))
    playout.command("queue_add", clip_ids=[first, second])

    (playout.clips_folder / "b.mp4").unlink()
    playout.soon(lambda: (playout.clip("b.mp4") or {}).get("missing") is True)
    result = run_module_checks(playout.services, "playout", playout.clock)["queue_files"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == "1 without a file: b"
    assert result.link == "/playout"


def test_free_space_is_checked_on_each_folders_drive(
    playout: PlayoutStudio, system_probes: FakeSystemProbes, tmp_path: Path
) -> None:
    roomy, tight = tmp_path / "roomy", tmp_path / "tight"
    system_probes.free_bytes_by_path[roomy] = 400 * GIGABYTE
    system_probes.free_bytes_by_path[tight] = 30 * GIGABYTE
    playout.configure(
        sources={"local_folders": [{"path": str(roomy)}, {"path": str(tight), "label": "Archive"}]}
    )

    results = run_module_checks(playout.services, "playout", playout.clock)

    assert results["disk:0"].status == CheckStatus.OK
    assert results["disk:1"].status == CheckStatus.WARN
    assert results["disk:1"].detail == f"30.0 GB free on the drive of Archive ({tight})"
    assert checks(playout)["disk:1"] == CheckStatus.WARN
