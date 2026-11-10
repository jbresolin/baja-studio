"""The playout module running inside the service, with fakes around it, for module tests.

Loaded into every test through `pytest_plugins` in conftest.py. The
`playout` fixture runs the service with only the playout module, on the fake
clock. Nothing real is opened: OBS is a `FakeObs` reached through the
in-process network, the orchestrator is a `FakeOrchestrator` behind an httpx2
mock transport, ffprobe/ffmpeg are a `FakeProber`, and the on-air webhook
lands in a list. Clips are real (tiny) files in a temporary folder, because
the scanner walks real folders. Free disk space comes from `system_probes`.
"""

import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from baja_studio.core.module import Module
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.services import StudioServices
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.fakes.fake_orchestrator import FakeOrchestrator
from baja_studio.modules.playout import PlayoutModule
from baja_studio.playout.config import ToolsSettings
from baja_studio.playout.probe import MediaInfo, ProbeError, Prober
from baja_studio.playout.tools import ResolvedTools, resolve_tools
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time,
    step_fake_time_until,
)
from tests.core.fake_system_probes import FakeSystemProbes

OBS_HOST = "obs.test"
OBS_PORT = 4455
# Playout never connects without a password, so the fake OBS asks for one.
OBS_PASSWORD = "obs-secret"
ORCHESTRATOR_URL = "http://orchestrator.test:8080"
ORCHESTRATOR_TOKEN = "studio-token"
HOOK_URL = "http://overlay.test/hook"
# The fake OBS plays every file for this long (the fake prober agrees).
CLIP_SECONDS = 10.0
# The fake prober's frame rate (a frame step on air moves 40 ms).
CLIP_FPS = 25.0
# All five playout workers sleep only on the fake clock.
PLAYOUT_WORKERS = module_worker_threads("playout")


@dataclass
class FakeProber:
    """Stands in for ffprobe/ffmpeg: every file is CLIP_SECONDS long at CLIP_FPS, unless failing."""

    failing: set[str] = field(default_factory=set)
    probed: list[str] = field(default_factory=list)
    thumbnails: list[Path] = field(default_factory=list)

    def probe(self, path: str) -> MediaInfo:
        self.probed.append(path)
        if Path(path).name in self.failing:
            raise ProbeError("Invalid data found when processing input")
        return MediaInfo(CLIP_SECONDS, CLIP_FPS)

    def make_thumbnail(self, path: str, output: Path, at_s: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\xff\xd8 fake jpeg")
        self.thumbnails.append(output)


@dataclass
class PlayoutStudio:
    """The running service plus the fakes around it."""

    services: StudioServices
    module: PlayoutModule
    clock: FakeClock
    network: InProcessObsNetwork
    obs: FakeObs
    orchestrator: FakeOrchestrator
    prober: FakeProber
    hook_posts: list[dict[str, Any]]
    clips_folder: Path
    # The query string of every request playout sent the orchestrator, oldest first.
    orchestrator_queries: list[dict[str, str]]
    # Sends HTTP requests to the running service (no network).
    http: TestClient
    # Which of ffprobe/ffmpeg the module finds (both, unless a test removes one).
    tools_found: set[str]

    def command(self, command: str, source: str = "web:alice", **args: Any) -> Any:
        return self.services.registry.dispatch_command("playout", command, args, source)

    def state(self, path: str = "") -> Any:
        return self.services.state.get("playout", path)

    def configure(self, **sections: Any) -> None:
        """Save settings sections the way the page does (whole sections at a time)."""
        self.services.settings.update("playout", sections)

    def obs_settings(self, **overrides: Any) -> dict[str, Any]:
        return {"host": OBS_HOST, "port": OBS_PORT, "password": OBS_PASSWORD, **overrides}

    def connect_obs(self, **overrides: Any) -> None:
        """Point playout at the fake OBS and wait until it reports `connected`."""
        self.configure(obs=self.obs_settings(**overrides))
        eventually(lambda: self.state("obs.connection") == "connected")

    def watch_folder(self) -> None:
        """Make the temporary clips folder the delivery folder."""
        folder = {"path": str(self.clips_folder), "label": "Delivery"}
        self.configure(sources={"local_folders": [folder]})

    def add_file(self, relative_path: str, content: bytes = b"video") -> Path:
        """Drop a file in the clips folder the way the receiver does.

        It is written as a dot-file (which the scanner skips) and renamed when
        complete, so a scan never sees it half-written.
        """
        path = self.clips_folder / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.part")
        partial.write_bytes(content)
        partial.replace(path)
        return path

    def clips(self) -> list[dict[str, Any]]:
        return list((self.state("library") or {}).values())

    def clip(self, relative_path: str) -> dict[str, Any] | None:
        return next((c for c in self.clips() if c["relative_path"] == relative_path), None)

    def wait_for_clip(self, relative_path: str) -> dict[str, Any]:
        """Wait until the library shows this file with a duration (scanned and probed)."""
        self.soon(lambda: (self.clip(relative_path) or {}).get("duration_s") is not None)
        clip = self.clip(relative_path)
        assert clip is not None
        return clip

    def soon(self, condition: Callable[[], bool], within_seconds: float = 6.0) -> None:
        """Let fake time pass in 0.1 s steps until `condition()` holds.

        The workers sleep on the fake clock (the scanner 5 s, the OBS poll
        0.2 s), so many things only happen as fake time passes.
        """
        step_fake_time_until(self.clock, condition, within_seconds, PLAYOUT_WORKERS)

    def pass_time(self, seconds: float) -> None:
        """Let `seconds` of fake time pass in 0.1 s steps, letting the workers run each step."""
        step_fake_time(self.clock, seconds, PLAYOUT_WORKERS)

    def log(self) -> list[tuple[str, str, str]]:
        """(source, command, result) of the playout log, newest first."""
        return [
            (entry["source"], entry["command"], entry["result"])
            for entry in self.services.command_log.tail("playout", 200)
        ]


@pytest.fixture
def playout_extra_modules() -> list[Module]:
    """Modules to run beside playout (none); a test file overrides this to add audio."""
    return []


@pytest.fixture
def playout(
    make_app: AppFactory,
    fake_clock: FakeClock,
    tmp_path: Path,
    system_probes: FakeSystemProbes,
    playout_extra_modules: list[Module],
) -> Iterator[PlayoutStudio]:
    network = InProcessObsNetwork()
    obs = FakeObs.with_playout_scenes(
        fake_clock, password=OBS_PASSWORD, duration_for=lambda _file: CLIP_SECONDS
    )
    network.plug(OBS_HOST, OBS_PORT, obs)
    orchestrator = FakeOrchestrator(ORCHESTRATOR_TOKEN)
    prober = FakeProber()
    hook_posts: list[dict[str, Any]] = []

    orchestrator_queries: list[dict[str, str]] = []
    orchestrator_transport = orchestrator.transport()

    def catch_hook(request: httpx2.Request) -> httpx2.Response:
        hook_posts.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(204)

    def record_and_forward(request: httpx2.Request) -> httpx2.Response:
        orchestrator_queries.append(dict(request.url.params))
        return orchestrator_transport.handle_request(request)

    def prober_factory(tools: ResolvedTools) -> Prober:
        return prober

    registries: list[ModuleRegistry] = []
    tools_found: set[str] = {"ffprobe", "ffmpeg"}

    def dispatch(module_name: str, command: str, args: dict[str, Any], source: str) -> Any:
        # On-air actions go through the registry, like any command.
        return registries[0].dispatch_command(module_name, command, args, source)

    def find_tool(command: str) -> str | None:
        # The bare names answer from `tools_found`; a typed path is looked up for real.
        if command in ("ffprobe", "ffmpeg"):
            return f"/opt/ffmpeg/{command}" if command in tools_found else None
        return shutil.which(command)

    def tool_resolver(tools: ToolsSettings) -> ResolvedTools:
        # The real resolver (bundled copy from the install folder in the
        # environment, else PATH), with only the PATH lookup faked.
        return resolve_tools(tools, which=find_tool)

    module = PlayoutModule(
        obs_transport_factory=network.connect,
        orchestrator_transport=httpx2.MockTransport(record_and_forward),
        prober_factory=prober_factory,
        hook_transport=httpx2.MockTransport(catch_hook),
        free_disk_bytes=system_probes.free_disk_bytes,
        dispatch=dispatch,
        tool_resolver=tool_resolver,
    )
    clips_folder = tmp_path / "clips"
    clips_folder.mkdir()

    def modules(registry: ModuleRegistry) -> list[Module]:
        registries.append(registry)
        return [module, *playout_extra_modules]

    app = make_app(modules)
    with TestClient(app) as http:
        yield PlayoutStudio(
            app.state.services,
            module,
            fake_clock,
            network,
            obs,
            orchestrator,
            prober,
            hook_posts,
            clips_folder,
            orchestrator_queries,
            http,
            tools_found,
        )


def ready_studio(
    playout: PlayoutStudio, files: tuple[str, ...] = ("a.mp4", "b.mp4", "c.mp4")
) -> list[int]:
    """OBS connected, the folder watched, `files` scanned and probed; returns their clip ids."""
    playout.connect_obs()
    for name in files:
        playout.add_file(name)
    playout.watch_folder()
    return [playout.wait_for_clip(name)["id"] for name in files]
