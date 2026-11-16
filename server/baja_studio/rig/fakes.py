"""The rig's fakes: which ones, on which ports, started how, and how to tell they are up.

Every fake is launched through its existing command line (`python -m
baja_studio.fakes.<fake> ...`), bound to 127.0.0.1 on a free port. The
M-VAVE is not a process: the studio's MIDI child uses its fake backend when
`BAJA_STUDIO_MIDI_BACKEND=fake` (`STUDIO_ENVIRONMENT`). Nor are the USB cameras
(two S600s and a Pixy, WP6a): the UVC child uses the fake duvc library when
`BAJA_STUDIO_UVC_BACKEND=fake`, and scenarios reach its switches through the
cameras module's `simulate_usb` command.

The fake MediaMTX (WP14) watches the `mediamtx.yml` the rig studio writes in
its data folder and writes MediaMTX-style lines where the studio reads the
real one's log; it opens only its control API, not the RTMP/SRT/RTSP ports.

The laptop camera node (WP15, ADR 0023) is the real node,
`python -m baja_studio.node --fake-video`: two cameras in node mode on fake
pipelines, as an agent with two crop cameras (its own data folder is under
the rig's logs, so every rig starts it afresh).

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
from baja_studio.childprocs.uvc_child import (
    BACKEND_ENVIRONMENT_VARIABLE as UVC_BACKEND_ENVIRONMENT_VARIABLE,
)
from baja_studio.modules.feeds.mediamtx_config import config_path
from baja_studio.modules.feeds.service_log import log_path
from baja_studio.rig.layout import LOOPBACK, free_tcp_port, free_udp_port
from baja_studio.rig.processes import ProcessStartError, RigProcess

OBS_PASSWORD = "rig-obs-password"
# A 4K webcam input in the fake OBS, with its Crop/Pad filter: the rig's OBS crop camera.
OBS_WEBCAM = "Rig webcam 4K"
OBS_CROP_FILTER = "Baja crop"
# A trackside laptop's OBS (WP13, ADR 0021): a second fake OBS with two 4K webcams,
# each with its Crop/Pad filter, driven by the studio's remote crop cameras.
LAPTOP_OBS_PASSWORD = "rig-laptop-obs-password"
LAPTOP_WEBCAMS = ("Laptop webcam A", "Laptop webcam B")
AGENT_TOKEN = "rig-agent-token"
ORCHESTRATOR_TOKEN = "rig-orchestrator-token"
XR18_NAME = "XR18-RIG"
# Agent ids as the studio's registry knows them; the fakes report the same.
AGENT_IDS = ("rig-laptop-a", "rig-laptop-b")
CAMERAS_PER_AGENT = 2
# The laptop camera node (WP15): its agent id, and how many fake-video cameras it serves.
NODE_AGENT_ID = "rig-node"
NODE_CAMERAS = 2
# The feeds the bundle configures; the fake MediaMTX has them publishing from the start.
FEED_SENDERS = ("drone", "laptop1")
# What the studio needs in its environment to drive the fake M-VAVE and USB cameras.
STUDIO_ENVIRONMENT = {
    BACKEND_ENVIRONMENT_VARIABLE: "fake",
    UVC_BACKEND_ENVIRONMENT_VARIABLE: "fake",
}
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
    laptop_obs: int
    # The laptop camera node's agent API (WP15).
    node: int = 8891
    # The fake MediaMTX's control API, and the (unopened) RTMP, SRT and RTSP ports
    # the feeds settings name.
    mediamtx_api: int = 9997
    feeds: tuple[int, int, int] = (1935, 8890, 8554)

    @classmethod
    def pick(cls) -> "FakePorts":
        """Free ports for every fake, chosen now."""
        return cls(
            obs=free_tcp_port(),
            xr18=free_udp_port(),
            cameras=(free_udp_port(), free_udp_port()),
            agents=(free_tcp_port(), free_tcp_port()),
            orchestrator=free_tcp_port(),
            laptop_obs=free_tcp_port(),
            node=free_tcp_port(),
            mediamtx_api=free_tcp_port(),
            feeds=(free_tcp_port(), free_udp_port(), free_tcp_port()),
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
    measured_visca: bool = False,
    studio_data: Path | None = None,
) -> dict[str, RigFake]:
    """Every fake, keyed by the name scenarios use (`obs`, `xr18`, `agent1`, ...); not started.

    `obs_seed` None turns the fake OBS's realistic timing off (`--fast-obs`);
    `ffprobe` (if found) lets it play the demo clips at their real length;
    `measured_visca` makes the VISCA cameras answer like the bench PTZ
    (`--measured-visca`: late, batched replies with their own sequence numbers);
    `studio_data` is the rig studio's data folder, where the fake MediaMTX
    finds the config the studio writes and leaves its log.
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
    obs_arguments += ["--video-source", f"{OBS_WEBCAM}=3840x2160", "--crop-filter", OBS_CROP_FILTER]
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
    laptop_arguments = ["--host", LOOPBACK, "--port", str(ports.laptop_obs)]
    laptop_arguments += ["--password", LAPTOP_OBS_PASSWORD, "--crop-filter", OBS_CROP_FILTER]
    for webcam in LAPTOP_WEBCAMS:
        laptop_arguments += ["--video-source", f"{webcam}=3840x2160"]
    add(
        "laptop-obs",
        "fake_obs",
        laptop_arguments,
        f"ws://{LOOPBACK}:{ports.laptop_obs} (a trackside laptop's OBS, password "
        f"{LAPTOP_OBS_PASSWORD}, two 4K webcams)",
        lambda: tcp_answers(ports.laptop_obs),
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
    camera_arguments = ["--measured"] if measured_visca else []
    camera_kind = "measured bench PTZ, " if measured_visca else ""
    for number, port in enumerate(ports.cameras, start=1):
        add(
            f"camera{number}",
            "fake_visca_camera",
            ["--host", LOOPBACK, "--port", str(port), *camera_arguments],
            f"VISCA udp://{LOOPBACK}:{port} "
            f"({camera_kind}direct fallback of agent {number}'s cam1)",
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
    node_arguments = ["--host", LOOPBACK, "--port", str(ports.node), "--fake-video"]
    node_arguments += ["--fake-cameras", str(NODE_CAMERAS), "--token", AGENT_TOKEN]
    node_arguments += ["--agent-id", NODE_AGENT_ID]
    node_arguments += ["--data-dir", str(log_directory / "node-data")]
    fakes["node"] = RigFake(
        RigProcess("node", "baja_studio.node", node_arguments, log_directory),
        f"http://{LOOPBACK}:{ports.node} (camera node {NODE_AGENT_ID}, two fake-video crop "
        "cameras; its page at /)",
        lambda: tcp_answers(ports.node),
    )
    mediamtx_arguments = ["--host", LOOPBACK, "--port", str(ports.mediamtx_api)]
    mediamtx_arguments += ["--senders", ",".join(FEED_SENDERS)]
    if studio_data is not None:
        mediamtx_arguments += ["--config", str(config_path(studio_data))]
        mediamtx_arguments += ["--log", str(log_path(studio_data))]
    add(
        "mediamtx",
        "fake_mediamtx",
        mediamtx_arguments,
        f"http://{LOOPBACK}:{ports.mediamtx_api}/v3 ({', '.join(FEED_SENDERS)} publishing)",
        lambda: tcp_answers(ports.mediamtx_api),
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
