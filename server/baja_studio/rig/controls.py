"""Scenario steps carried out on the running fakes, through their own control surfaces.

The XR18 through its console (standard input), the agents through their
`/_fake` switches, any fake by stopping its process and starting it again on
the same port (how the `obs-restart` scenario closes and reopens OBS).
"""

import httpx2

from baja_studio.rig.fakes import RigFake
from baja_studio.rig.layout import LOOPBACK
from baja_studio.rig.scenarios import Step

SWITCH_TIMEOUT_SECONDS = 5.0


class FakeControls:
    """`Controls` for the rig's real fakes."""

    def __init__(self, fakes: dict[str, RigFake], agent_ports: dict[str, int]) -> None:
        """`agent_ports` maps `agent1`/`agent2` to their HTTP port."""
        self._fakes = fakes
        self._agent_ports = agent_ports

    def perform(self, step: Step) -> None:
        """Carry out `step` (see `scenarios.py` for what each action means)."""
        fake = self._fakes[step.target]
        if step.action == "console":
            fake.process.type_line(str(step.args["line"]))
        elif step.action == "switch":
            port = self._agent_ports[step.target]
            response = httpx2.post(
                f"http://{LOOPBACK}:{port}/_fake/{step.args['switch']}",
                json=step.args.get("body", {}),
                timeout=SWITCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        elif step.action == "stop":
            fake.process.stop()
        elif step.action == "start":
            fake.process.start()
        else:
            raise ValueError(f"{step.action} steps are expanded before they are played")


def agent_ports(fakes: dict[str, RigFake], ports: tuple[int, ...]) -> dict[str, int]:
    """`{"agent1": port, "agent2": port}` for `FakeControls`."""
    names = sorted(name for name in fakes if name.startswith("agent"))
    return dict(zip(names, ports, strict=True))
