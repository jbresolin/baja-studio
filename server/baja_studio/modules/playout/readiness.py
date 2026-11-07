"""Playout's readiness checks: OBS and its scenes, the clip folders and tools, the queue.

OBS is asked only read-only questions (`GetSceneList`, `GetSceneItemId`,
`GetSceneTransitionList`) on the connection the `obs` worker already holds;
when OBS is not connected, the scene check says so as a warning, so one
stopped OBS is one red line, not four.
"""

import shutil
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from baja_studio.adapters.obs_transport import ObsError, ObsRequestFailedError
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.core.system_probes import disk_space_outcome, writable_problem
from baja_studio.modules.playout.controller import PlayoutController
from baja_studio.playout.config import PlayoutSettings

PLAYOUT_SETTINGS_LINK = "/settings/playout"
PLAYOUT_PAGE_LINK = "/playout"


def playout_checks(
    settings: PlayoutSettings,
    client: ObsWebsocketClient,
    controller: PlayoutController,
    playout_state: dict[str, Any],
    free_disk_bytes: Callable[[Path], int],
) -> list[ReadinessCheck]:
    """The checks for the settings as they are now (one disk check per local folder).

    `playout_state` is the module's state subtree, read once when the run starts.
    """
    obs = settings.obs
    checks = [
        ReadinessCheck(
            "obs",
            "OBS is connected",
            lambda: _obs_connected(playout_state.get("obs") or {}),
            fix=(
                f"Start OBS on {obs.host or 'the streaming PC'} with its WebSocket server on "
                f"(Tools → WebSocket Server Settings, port {obs.port}); the password must match "
                "Settings → Playout."
            ),
            link=PLAYOUT_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "obs_scenes",
            "OBS has playout's scenes, sources and transition",
            lambda: _obs_scenes(settings, client),
            fix=(
                f"In OBS, create scenes {obs.scene_a!r} and {obs.scene_b!r} holding media sources "
                f"{obs.source_a!r} and {obs.source_b!r} (docs/bench/wp5.md), or correct the names "
                "in Settings → Playout."
            ),
            link=PLAYOUT_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "clip_root",
            "Clip folder exists and is writable",
            lambda: _clip_root(settings),
            fix=(
                "Create the folder (or plug in its drive), or set the delivery folder as the "
                "first local folder in Settings → Playout."
            ),
            link=PLAYOUT_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "tools",
            "ffprobe and ffmpeg are found",
            lambda: _tools(settings),
            fix=(
                "Install ffmpeg (it brings ffprobe) and put the full paths of both in "
                "Settings → Playout → tools."
            ),
            link=PLAYOUT_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "orchestrator",
            "Orchestrator answers",
            lambda: _orchestrator(settings, playout_state.get("sync_status") or {}),
            fix=(
                "Check the PC is online and on the tailnet, and the orchestrator's address and "
                "token in Settings → Playout; local clips play meanwhile."
            ),
            link=PLAYOUT_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "queue_files",
            "Every queued clip has its file",
            lambda: _queue_files(controller),
            fix="Remove those clips from the queue, or put their files back in the clip folder.",
            link=PLAYOUT_PAGE_LINK,
        ),
    ]
    for index, folder in enumerate(settings.sources.local_folders):
        where = folder.label or folder.path
        checks.append(
            ReadinessCheck(
                f"disk:{index}",
                f"Free space for {where}",
                partial(_disk, where, Path(folder.path), free_disk_bytes),
                fix="Free space on that drive, or move old clips to another drive.",
            )
        )
    return checks


def _disk(where: str, path: Path, free_disk_bytes: Callable[[Path], int]) -> CheckOutcome:
    return disk_space_outcome(f"{where} ({path})", free_disk_bytes(path))


def _obs_connected(obs: dict[str, Any]) -> CheckOutcome:
    if obs.get("connection") == "connected":
        return passed(f"connected to {obs.get('address', 'OBS')}")
    return failed(f"{obs.get('connection')}: {obs.get('message') or 'no answer'}")


def _obs_scenes(settings: PlayoutSettings, client: ObsWebsocketClient) -> CheckOutcome:
    obs = settings.obs
    if not client.is_connected:
        return warning("not checked: OBS is not connected (see the line above)")
    try:
        scenes = set(client.get_scene_list().scene_names)
        problems = [
            f"no scene {name!r}" for name in (obs.scene_a, obs.scene_b) if name not in scenes
        ]
        for scene, source in ((obs.scene_a, obs.source_a), (obs.scene_b, obs.source_b)):
            if scene in scenes and not _scene_has(client, scene, source):
                problems.append(f"scene {scene!r} has no source {source!r}")
        if obs.return_scene and obs.return_scene not in scenes:
            problems.append(f"no return scene {obs.return_scene!r}")
        transitions = client.get_transition_names()
    except ObsError as error:
        return failed(f"OBS did not answer: {error}")
    if obs.cut_transition not in transitions:
        problems.append(
            f"no transition {obs.cut_transition!r} (OBS has {', '.join(transitions) or 'none'})"
        )
    if problems:
        return failed("; ".join(problems))
    if not obs.return_scene and obs.post_clip != "hold":
        return warning(
            "no return scene is set: after a clip the program stays where it is",
            "Set the return scene in Settings → Playout.",
        )
    return passed(f"{obs.scene_a}, {obs.scene_b}, transition {obs.cut_transition}")


def _scene_has(client: ObsWebsocketClient, scene: str, source: str) -> bool:
    try:
        client.get_scene_item_id(scene, source)
    except ObsRequestFailedError:
        return False
    return True


def _clip_root(settings: PlayoutSettings) -> CheckOutcome:
    folders = settings.sources.local_folders
    if not folders:
        return failed("no local folder is set: delivered clips have nowhere to go")
    problem = writable_problem(Path(folders[0].path))
    return failed(problem) if problem else passed(folders[0].path)


def _tools(settings: PlayoutSettings) -> CheckOutcome:
    tools = settings.tools
    paths = (tools.ffprobe_path, tools.ffmpeg_path)
    missing = [path for path in paths if shutil.which(path) is None]
    if missing:
        return failed(f"not found: {', '.join(missing)}")
    return passed(f"{shutil.which(tools.ffprobe_path)}, {shutil.which(tools.ffmpeg_path)}")


def _orchestrator(settings: PlayoutSettings, status: dict[str, Any]) -> CheckOutcome:
    if not settings.sources.orchestrator.enabled:
        return passed("not used (local clips only)")
    if status.get("state") == "ok":
        return passed(f"last manifest at {status.get('last_success_at')}")
    # The event can run on local clips: never a reason to call the studio not ready.
    return warning(f"{status.get('state')}: {status.get('error') or 'no manifest yet'}")


def _queue_files(controller: PlayoutController) -> CheckOutcome:
    entries = controller.queue.entries()
    missing = []
    for entry in entries:
        clip = controller.library.get(entry["clip_id"])
        if clip is None or clip["missing"]:
            missing.append(clip["title"] if clip else f"clip {entry['clip_id']}")
    if missing:
        return failed(f"{len(missing)} without a file: {', '.join(missing)}")
    return passed(f"{len(entries)} queued, all files present")
