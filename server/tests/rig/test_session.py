"""Integration tests of the whole rig (rig/session.py): real fakes on real loopback ports.

These are the one place outside the agent conformance suite where the tests
open sockets: every fake runs as its own process on 127.0.0.1, exactly as
`python -m baja_studio.rig` starts them. They are marked `rig` and kept out
of the default run (`pytest -m rig` runs them; CI does so on both OSes).

The studio runs in this test's process instead of as a child, for one
reason: its view of the PC (free disk, NTP, CPU) comes from the fake probes,
so a CI runner with little disk still gets an all-green readiness run. It
is set up exactly as the rig sets up its own studio, over the same HTTP API.
"""

import socket
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from baja_studio.adapters.osc import OscMessage, encode_message
from baja_studio.adapters.xr18_addresses import INFO
from baja_studio.cameras.drivers import visca_packets
from baja_studio.core.module import Module
from baja_studio.core.registry import ModuleRegistry
from baja_studio.main import build_module_list, create_app
from baja_studio.rig import fakes
from baja_studio.rig.clips import DemoClip
from baja_studio.rig.layout import LOOPBACK, RigLayout
from baja_studio.rig.scenarios import load_scenario
from baja_studio.rig.session import RigSession
from baja_studio.rig.studio import StudioApi, not_ok_checks
from tests.core.fake_system_probes import FakeSystemProbes
from tests.modules.gateway_studio import GatewayFakes

pytestmark = pytest.mark.rig

# Two short clips are enough to fill a library group and the receiver root.
SHORT_PLAN = (
    DemoClip("delivery/enduro/42/1.mp4", 2, tone_hz=440),
    DemoClip("patrocinadores/vinheta.mp4", 2),
)
# Generous: the studio links two agents, reads the mixer within its packet
# budget and syncs the manifest before every check can be green.
ALL_GREEN_DEADLINE_SECONDS = 45.0


@pytest.fixture
def session(tmp_path: Path) -> Iterator[RigSession]:
    """Every fake started on free ports, the bundle written; all stopped afterwards."""
    messages: list[str] = []
    rig = RigSession(RigLayout(tmp_path / "rig"), messages.append, obs_seed=1, clip_plan=SHORT_PLAN)
    rig.layout.create()
    try:
        rig.make_clips()
        rig.start_fakes()
        rig.write_bundle()
        yield rig
    finally:
        rig.stop()


@pytest.fixture
def studio(
    session: RigSession,
    tmp_path: Path,
    gateway_fakes: GatewayFakes,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[StudioApi]:
    """A real studio (real clock, real network clients) with the fake PC probes."""
    # The studio's MIDI child picks the fake M-VAVE from its environment, as the rig sets it.
    for name, value in fakes.STUDIO_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    probes = FakeSystemProbes()

    def modules(registry: ModuleRegistry) -> list[Module]:
        return build_module_list(
            registry, gateway_fakes.services, gateway_fakes.system(), system_probes=probes.probes()
        )

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    app = create_app(
        data_directory=tmp_path / "studio",
        frontend_directory=frontend,
        log_to_file=False,
        module_list_builder=modules,
    )
    with TestClient(app) as client:
        yield StudioApi(client)


def test_the_studio_set_up_by_the_rig_is_all_green(session: RigSession, studio: StudioApi) -> None:
    # Set the studio up as the rig does: import the bundle, restore the audio
    # defaults, choose the return scene.
    results = studio.import_bundle(session.layout.bundle.read_bytes())
    assert {item["section"] for item in results if item["result"] == "applied"} >= {
        "agents",
        "cameras",
        "playout",
        "audio",
        "hosts",
        "gateway",
    }
    studio.restore_audio_defaults()
    studio.set_return_scene()

    # A hosts screen for every seat (in real use, the hosts' monitor at /hosts).
    # Then run the readiness checks until all are green: the studio needs a few
    # seconds to link the agents, read the mixer and fetch the manifest.
    deadline = time.monotonic() + ALL_GREEN_DEADLINE_SECONDS
    while True:
        studio.command("hosts", "register_screen", {"seats": [1, 2, 3, 4]})
        problems = not_ok_checks(studio.run_readiness())
        if not problems or time.monotonic() > deadline:
            break
        time.sleep(1.0)
    assert problems == []


def test_a_compressed_flaky_agent_flips_the_fakes_switches_and_back(session: RigSession) -> None:
    # flaky-agent at a tenth of its speed: agent 2 goes silent at 3 s and back at 4.5 s.
    agent_two = f"http://{LOOPBACK}:{session.ports.agents[1]}/_fake"
    session.play(load_scenario("flaky-agent"), seed=1, time_scale=0.1)

    switches_seen = set()
    deadline = time.monotonic() + 15.0
    while not session.wait_for_scenario(0.2) and time.monotonic() < deadline:
        switches = httpx2.get(agent_two, timeout=5.0).json()
        switches_seen.add((switches["silent"], switches["delay_ms"]))
    assert session.wait_for_scenario(5.0)

    # It was silent and slow for a while, and is answering normally at the end.
    assert (True, 5000) in switches_seen
    assert httpx2.get(agent_two, timeout=5.0).json() == {
        "silent": False,
        "state_frozen": False,
        "delay_ms": 0,
    }


def this_pcs_network_address() -> str | None:
    """This PC's address on its network (not 127.x), or None when it has none.

    "Connecting" a UDP socket sends nothing; it only makes the system pick the
    address it would send from. 192.0.2.1 is a documentation address.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def answers_at(host: str, port: int, datagram: bytes | None) -> bool:
    """True when `host:port` accepts a TCP connection (or, with `datagram`, answers it over UDP)."""
    if datagram is None:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.settimeout(1.0)
        try:
            probe.sendto(datagram, (host, port))
            probe.recvfrom(65535)
        except OSError:  # refused (Windows reports it as a reset) or no answer
            return False
    return True


def test_no_fake_can_be_reached_from_another_pc(session: RigSession) -> None:
    # The bundle's passwords and tokens are plain text because of this test: each
    # fake answers on 127.0.0.1, and nothing answers on this PC's network address,
    # which is where another PC would reach it.
    network_address = this_pcs_network_address()
    if network_address is None:
        pytest.skip("this PC has no network address to try from")
    xinfo = encode_message(OscMessage(INFO, ()))
    inquiry = visca_packets.wrap(
        visca_packets.pan_tilt_position_inquiry(), visca_packets.PAYLOAD_INQUIRY, 1
    )
    ports = session.ports
    listening = {
        "obs": (ports.obs, None),
        "xr18": (ports.xr18, xinfo),
        "camera1": (ports.cameras[0], inquiry),
        "camera2": (ports.cameras[1], inquiry),
        "agent1": (ports.agents[0], None),
        "agent2": (ports.agents[1], None),
        "orchestrator": (ports.orchestrator, None),
    }
    assert set(listening) == set(session.fakes)

    for name, (port, datagram) in listening.items():
        assert answers_at(LOOPBACK, port, datagram), f"{name} does not answer on 127.0.0.1"
        assert not answers_at(network_address, port, datagram), (
            f"{name} answers on {network_address}:{port}: another PC could reach it"
        )


def test_stopping_the_rig_leaves_no_child_running(tmp_path: Path) -> None:
    # The Ctrl-C path: `stop` is what the rig's `finally` runs.
    rig = RigSession(RigLayout(tmp_path / "rig"), lambda message: None, obs_seed=None, clip_plan=())
    rig.layout.create()
    rig.start_fakes()
    processes = [fake.process for fake in rig.fakes.values()]
    assert all(process.running for process in processes)

    rig.stop()

    assert not any(process.running for process in processes)
    # Their ports are free again: nothing answers on any of them.
    assert not any(fakes.tcp_answers(port) for port in (rig.ports.obs, *rig.ports.agents))
