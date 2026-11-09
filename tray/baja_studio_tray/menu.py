"""The tray menu as plain data; app.py turns it into pystray items.

Order (brief §3): Open Baja Studio (also the double-click), Readiness,
Restart services, Open logs folder, Diagnostics bundle, the version, Quit.
"""

from collections.abc import Callable
from dataclasses import dataclass

from baja_studio_tray.actions import TrayActions
from baja_studio_tray.labels import Labels


@dataclass(frozen=True)
class MenuEntry:
    """One line of the menu; `action` None draws a separator or a greyed-out line."""

    text: Callable[[], str]
    action: Callable[[], None] | None
    default: bool = False
    separator: bool = False


SEPARATOR = MenuEntry(text=lambda: "", action=None, separator=True)


def menu_entries(
    labels: Labels,
    actions: TrayActions,
    version: Callable[[], str | None],
    quit_tray: Callable[[], None],
) -> list[MenuEntry]:
    """The menu; `version()` is read each time it opens, so it follows the last poll."""
    return [
        MenuEntry(lambda: labels.open_studio, actions.open_studio, default=True),
        MenuEntry(lambda: labels.readiness, actions.open_readiness),
        SEPARATOR,
        MenuEntry(lambda: labels.restart_services, actions.restart_services),
        MenuEntry(lambda: labels.open_logs, actions.open_logs),
        MenuEntry(lambda: labels.diagnostics_bundle, actions.diagnostics_bundle),
        SEPARATOR,
        MenuEntry(lambda: labels.version_item(version()), None),
        MenuEntry(lambda: labels.quit, quit_tray),
    ]
