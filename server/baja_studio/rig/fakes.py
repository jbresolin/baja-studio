"""The rig's fakes: which ones, on which ports, started how, and how to tell they are up.

Every fake is launched through its existing command line (`python -m
baja_studio.fakes.<fake> ...`), bound to 127.0.0.1 on a free port. The
M-VAVE is not a process: the studio's MIDI child uses its fake backend when
`BAJA_STUDIO_MIDI_BACKEND=fake` (`STUDIO_ENVIRONMENT`).

The passwords and tokens below protect nothing but these local fakes, so they
are plain constants (and travel in the settings bundle; ADR 0016).
"""

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from baja_studio.adapters.osc import OscMessage, encode_message
from baja_studio.adapters.xr18_addresses import INFO
from baja_studio.cameras.drivers import visca_packets
from baja_studio.childprocs.midi_child import BACKEND_ENVIRONMENT_VARIABLE
from baja_studio.rig.layout import LOOPBACK, free_tcp_port, free_udp_port
from baja_studio.rig.processes import ProcessStartError, RigProcess

OBS_PASSWORD = "rig-obs-password"
AGENT_TOKEN = "rig-agent-token"
ORCHESTRATOR_TOKEN = "rig-orchestrator-token"
XR18_NAME = "XR18-RIG"
# Agent ids as the studio's registry knows them; the fakes report the same.
AGENT_IDS = ("rig-laptop-a", "rig-laptop-b")
CAMERAS_PER_AGENT = 2
# What the studio needs in its environment to drive the fake M-VAVE.
STUDIO_ENVIRONMENT = {BACKEND_ENVIRONMENT_VARIABLE: "fake"}
READY_TIMEOUT_SECONDS = 20.0
PROBE_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True)
class FakePorts:
    """Where each fake listens, all on 127.0.0.1."""

    obs: int
    xr18: int
    cameras: tuple[int, int]
    agents: tuple[int, int]
    orchestrator: int

    @classmethod
    def pick(cls) -> "FakePorts":
        """Free ports for every fake, chosen now."""
        return cls(
            obs=free_tcp_port(),
            xr18=free_udp_port(),
            cameras=(free_udp_port(), free_udp_port()),
            agents=(free_tcp_port(), free_tcp_port()),
            orchestrator=free_tcp_port(),
        )


@dataclass
class RigFake:
    """One running fake: its process, what to print about it, and how to see it is up."""

    process: RigProcess
    describe: str
    is_up: Callable[[], bool]


def build_fakes(
    ports: FakePorts,
    delivery_folder: Path,
    log_directory: Path,
    obs_seed: int | None,
    ffprobe: str | None,
) -> dict[str, RigFake]:
    """Every fake, keyed by the name scenarios use (`obs`, `xr18`, `agent1`, ...); not started.

    `obs_seed` None turns the fake OBS's realistic timing off (`--fast-obs`);
    `ffprobe` (if found) lets it play the demo clips at their real length.
    """
    fakes: dict[str, RigFake] = {}

    def add(
        name: str,
        module: str,
        arguments: list[str],
        describe: str,
        is_up: Callable[[], bool],
        console: bool = False,
    ) -> None:
        process = RigProcess(
            name, f"baja_studio.fakes.{module}", arguments, log_directory, console=console
        )
        fakes[name] = RigFake(process, describe, is_up)

    obs_arguments = ["--host", LOOPBACK, "--port", str(ports.obs), "--password", OBS_PASSWORD]
    if obs_seed is not None:
        obs_arguments += ["--seed", str(obs_seed)]
    if ffprobe:
        obs_arguments += ["--ffprobe", ffprobe]
    timing = f"seed {obs_seed}" if obs_seed is not None else "no timing (--fast-obs)"
    add(
        "obs",
        "fake_obs",
        obs_arguments,
        f"ws://{LOOPBACK}:{ports.obs} (password {OBS_PASSWORD}, {timing})",
        lambda: tcp_answers(ports.obs),
    )
    add(
        "xr18",
        "fake_xr18",
        ["--host", LOOPBACK, "--port", str(ports.xr18), "--name", XR18_NAME],
        f"udp://{LOOPBACK}:{ports.xr18} (console: the rig's scenarios)",
        lambda: udp_answers(ports.xr18, encode_message(OscMessage(INFO, ()))),
        console=True,
    )
    inquiry = visca_packets.wrap(
        visca_packets.pan_tilt_position_inquiry(), visca_packets.PAYLOAD_INQUIRY, 1
    )
    for number, port in enumerate(ports.cameras, start=1):
        add(
            f"camera{number}",
            "fake_visca_camera",
            ["--host", LOOPBACK, "--port", str(port)],
            f"VISCA udp://{LOOPBACK}:{port} (direct fallback of agent {number}'s cam1)",
            lambda port=port: udp_answers(port, inquiry),  # type: ignore[misc]
        )
    for number, (agent_id, port) in enumerate(zip(AGENT_IDS, ports.agents, strict=True), start=1):
        add(
            f"agent{number}",
            "fake_agent",
            [
                "--host",
                LOOPBACK,
                "--port",
                str(port),
                "--token",
                AGENT_TOKEN,
                "--agent-id",
                agent_id,
                "--name",
                f"Rig laptop {number}",
                "--cameras",
                str(CAMERAS_PER_AGENT),
            ],
            f"http://{LOOPBACK}:{port} ({agent_id}, switches under /_fake)",
            lambda port=port: tcp_answers(port),  # type: ignore[misc]
        )
    add(
        "orchestrator",
        "fake_orchestrator",
        [
            "--host",
            LOOPBACK,
            "--port",
            str(ports.orchestrator),
            "--token",
            ORCHESTRATOR_TOKEN,
            "--from-folder",
            str(delivery_folder),
        ],
        f"http://{LOOPBACK}:{ports.orchestrator}/manifest",
        lambda: tcp_answers(ports.orchestrator),
    )
    return fakes


def wait_until_up(fakes: dict[str, RigFake], timeout_s: float = READY_TIMEOUT_SECONDS) -> None:
    """Wait for every fake to answer; raises `ProcessStartError` naming the first that does not."""
    deadline = time.monotonic() + timeout_s
    for name, fake in fakes.items():
        while not fake.is_up():
            if not fake.process.running:
                raise ProcessStartError(f"{name} quit at start; see {fake.process.log_path}")
            if time.monotonic() > deadline:
                raise ProcessStartError(f"{name} did not answer; see {fake.process.log_path}")
            time.sleep(PROBE_INTERVAL_SECONDS)


def tcp_answers(port: int) -> bool:
    """True when something accepts a TCP connection on the loopback port."""
    try:
        with socket.create_connection((LOOPBACK, port), timeout=PROBE_INTERVAL_SECONDS):
            return True
    except OSError:
        return False


def udp_answers(port: int, datagram: bytes) -> bool:
    """True when the loopback UDP port answers `datagram` with anything."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.settimeout(PROBE_INTERVAL_SECONDS)
        try:
            probe.sendto(datagram, (LOOPBACK, port))
            probe.recvfrom(65535)
        except OSError:  # nothing listening (Windows says so with a reset), or no answer yet
            return False
    return True
