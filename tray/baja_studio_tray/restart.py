"""Restart both services, in the order the installer starts them (run elevated).

The tray starts this through UAC (`ShellExecute "runas"`) when the operator
picks *Restart services*:

    pythonw -m baja_studio_tray.restart

The gateway (Caddy) goes down first, because it depends on the studio; the
studio restarts; the gateway starts again once the studio answers
`/api/health` (it writes the Caddyfile before it answers, ADR 0010), or after
60 s regardless, since NSSM keeps retrying it anyway.
"""

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from baja_studio_tray.service import (
    GATEWAY_SERVICE,
    STUDIO_SERVICE,
    StudioClient,
    read_install_paths,
)

HEALTH_WAIT_SECONDS = 60.0
HEALTH_POLL_SECONDS = 1.0
NSSM_TIMEOUT_SECONDS = 60.0

Runner = Callable[[Sequence[str]], int]


def run_hidden(command: Sequence[str]) -> int:
    """Run a command with no console window; its exit code."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        list(command),
        capture_output=True,
        timeout=NSSM_TIMEOUT_SECONDS,
        creationflags=flags,
        check=False,
    )
    return completed.returncode


def wait_until_answering(
    client: StudioClient,
    timeout: float = HEALTH_WAIT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll `/api/health` until it answers; False after `timeout` seconds."""
    deadline = monotonic() + timeout
    while True:
        if client.health() is not None:
            return True
        if monotonic() >= deadline:
            return False
        sleep(HEALTH_POLL_SECONDS)


def restart_services(
    nssm: Path,
    client: StudioClient,
    run: Runner = run_hidden,
    wait: Callable[[StudioClient], bool] = wait_until_answering,
) -> bool:
    """Gateway down, studio restarted, gateway up once the studio answers; True if it did."""
    run([str(nssm), "stop", GATEWAY_SERVICE])
    restarted = run([str(nssm), "restart", STUDIO_SERVICE]) == 0
    answering = wait(client)
    started = run([str(nssm), "start", GATEWAY_SERVICE]) == 0
    return restarted and answering and started


def main() -> None:
    """Entry point for the elevated process; exit code 0 when both are back."""
    paths = read_install_paths()
    ok = restart_services(paths.nssm_executable, StudioClient())
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
