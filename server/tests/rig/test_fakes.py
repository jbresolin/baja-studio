"""Tests for how the rig launches its fakes (rig/fakes.py).

The settings bundle carries the fakes' passwords and tokens in plain text
(ADR 0016). That is acceptable only because nothing the rig starts can be
reached from another PC: every fake, and the rig's studio, listens on
127.0.0.1 alone. These tests pin that down from the command lines; the
`rig` integration tests (test_session.py) check the real sockets too.
"""

from pathlib import Path

from baja_studio.rig.fakes import FakePorts, build_fakes
from baja_studio.rig.studio import studio_process

PORTS = FakePorts(
    obs=4455, xr18=10024, cameras=(52381, 52382), agents=(8890, 8891), orchestrator=8000
)


def host_argument(command: list[str]) -> str | None:
    """The value after `--host` on a command line, or None when it has none."""
    if "--host" not in command:
        return None
    return command[command.index("--host") + 1]


def test_every_fake_the_rig_starts_listens_on_loopback_only(tmp_path: Path) -> None:
    fakes = build_fakes(PORTS, tmp_path / "delivery", tmp_path / "logs", 1, None)

    # The rig starts all seven fakes ...
    assert set(fakes) == {
        "obs",
        "xr18",
        "camera1",
        "camera2",
        "agent1",
        "agent2",
        "orchestrator",
    }
    # ... and each is told to listen on 127.0.0.1. Without `--host` several fakes
    # would listen on every network card (0.0.0.0 is their default).
    for name, fake in fakes.items():
        assert host_argument(fake.process.command) == "127.0.0.1", name


def test_the_rigs_studio_listens_on_loopback_only(tmp_path: Path) -> None:
    # The studio's own default is every network card; the rig's is not.
    studio = studio_process(tmp_path / "studio", tmp_path / "logs")
    assert host_argument(studio.command) == "127.0.0.1"
