"""`pythonw -m baja_studio_tray`: the tray icon, started at logon by the installer's Run entry."""

import argparse
from collections.abc import Callable

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.app import IconBackend, Tray, already_running, run_tray
from baja_studio_tray.labels import system_labels
from baja_studio_tray.service import StudioClient, read_install_paths


def main(
    arguments: list[str] | None = None,
    client: StudioClient | None = None,
    backend: IconBackend | None = None,
    on_started: Callable[[Tray], None] | None = None,
) -> None:
    """Show the tray icon until Quit; one per logon session.

    `client`, `backend` and `on_started` are for tests (a fake service and icon).
    """
    parser = argparse.ArgumentParser(prog="baja_studio_tray", description=__doc__)
    parser.add_argument(
        "--exit-after-first-poll",
        action="store_true",
        help="show the icon, poll once, print the status and exit (the installer's smoke test)",
    )
    options = parser.parse_args(arguments)
    if already_running() and not options.exit_after_first_poll:
        return
    labels = system_labels()
    client = client if client is not None else StudioClient()
    paths = read_install_paths()
    status = run_tray(
        client,
        labels,
        lambda notify: TrayActions(client, paths, labels, notify),
        exit_after_first_poll=options.exit_after_first_poll,
        backend=backend,
        on_started=on_started,
    )
    if options.exit_after_first_poll:
        if status is None:
            raise SystemExit("the tray never polled")
        print(f"badge={status.badge.value} version={status.version or ''}")
        print(f"tooltip={status.tooltip(labels)}")


if __name__ == "__main__":
    main()
