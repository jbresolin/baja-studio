"""`pythonw -m baja_studio_tray [--node]`: the tray icon, started at logon (installer's Run entry).

Without `--node` it is Baja Studio's tray; with it, the camera node's (a
camera icon, ADR 0023), which the node installer starts instead.
"""

import argparse
from collections.abc import Callable

from baja_studio_tray.actions import NodeTrayActions, TrayActions
from baja_studio_tray.app import IconBackend, Tray, already_running, run_tray
from baja_studio_tray.labels import Labels, system_labels
from baja_studio_tray.menu import node_menu_entries
from baja_studio_tray.profile import NODE, STUDIO
from baja_studio_tray.service import (
    NodeClient,
    StatusSource,
    StudioClient,
    port_from_file,
    read_install_paths,
)
from baja_studio_tray.status import TrayStatus


def main(
    arguments: list[str] | None = None,
    client: StatusSource | None = None,
    backend: IconBackend | None = None,
    on_started: Callable[[Tray], None] | None = None,
) -> None:
    """Show the tray icon until Quit; one per logon session (and program).

    `client`, `backend` and `on_started` are for tests (a fake service and icon).
    """
    parser = argparse.ArgumentParser(prog="baja_studio_tray", description=__doc__)
    parser.add_argument(
        "--exit-after-first-poll",
        action="store_true",
        help="show the icon, poll once, print the status and exit (the installer's smoke test)",
    )
    parser.add_argument(
        "--node", action="store_true", help="the camera node's tray instead of Baja Studio's"
    )
    options = parser.parse_args(arguments)
    profile = NODE if options.node else STUDIO
    if already_running(profile.single_instance_mutex) and not options.exit_after_first_poll:
        return
    labels = system_labels()
    paths = read_install_paths(profile=profile)
    if options.node:
        port = port_from_file(paths.data_directory)
        node_client = client if client is not None else NodeClient(port)
        status = run_tray(
            node_client,
            labels,
            lambda notify: NodeTrayActions(paths, labels, notify, port),
            exit_after_first_poll=options.exit_after_first_poll,
            backend=backend,
            on_started=on_started,
            profile=NODE,
            make_menu=lambda actions, poller, quit_tray: node_menu_entries(
                labels, actions, poller, quit_tray
            ),
        )
    else:
        studio_client = client if client is not None else StudioClient()
        assert isinstance(studio_client, StudioClient)
        status = run_tray(
            studio_client,
            labels,
            lambda notify: TrayActions(studio_client, paths, labels, notify),
            exit_after_first_poll=options.exit_after_first_poll,
            backend=backend,
            on_started=on_started,
        )
    if options.exit_after_first_poll:
        report(status, labels)


def report(status: TrayStatus | None, labels: Labels) -> None:
    """What the installer's smoke test reads: the badge, the version and the tooltip."""
    if status is None:
        raise SystemExit("the tray never polled")
    print(f"badge={status.badge.value} version={status.version or ''}")
    print(f"tooltip={status.tooltip(labels)}")
    for line in status.camera_lines:
        print(f"camera={line}")


if __name__ == "__main__":
    main()
