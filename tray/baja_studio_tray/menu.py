"""The tray menu as plain data; app.py turns it into pystray items.

The studio's (brief §3): Open Baja Studio (also the double-click), Readiness,
Restart services, Open logs folder, Diagnostics bundle, the version, Quit.

The camera node's (`--node`, ADR 0023): Open the camera node's page (also
the double-click), one greyed-out line per camera ("Left S600 (node): live"),
Restart the camera node, Open logs folder, the version, Quit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from baja_studio_tray.actions import NodeTrayActions, TrayActions
from baja_studio_tray.labels import Labels

if TYPE_CHECKING:  # app.py imports this file
    from baja_studio_tray.app import StatusPoller

# A tray menu cannot grow or shrink once shown, so the camera lines are this
# many fixed slots, each hidden while there is no camera for it. A laptop has
# one or two cameras; six is room to spare.
CAMERA_LINE_SLOTS = 6


@dataclass(frozen=True)
class MenuEntry:
    """One line of the menu; `action` None draws a separator or a greyed-out line.

    `visible`, when given, is asked each time the menu opens (None: always shown).
    """

    text: Callable[[], str]
    action: Callable[[], None] | None
    default: bool = False
    separator: bool = False
    visible: Callable[[], bool] | None = None


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


def node_menu_entries(
    labels: Labels,
    actions: NodeTrayActions,
    poller: "StatusPoller",
    quit_tray: Callable[[], None],
) -> list[MenuEntry]:
    """The camera node's menu; the camera lines and the version follow the last poll."""
    return [
        MenuEntry(lambda: labels.open_node, actions.open_page, default=True),
        *camera_line_entries(poller.camera_lines),
        SEPARATOR,
        MenuEntry(lambda: labels.restart_node, actions.restart_node),
        MenuEntry(lambda: labels.open_logs, actions.open_logs),
        SEPARATOR,
        MenuEntry(lambda: labels.version_item(poller.version()), None),
        MenuEntry(lambda: labels.quit, quit_tray),
    ]


def camera_line_entries(lines: Callable[[], tuple[str, ...]]) -> list[MenuEntry]:
    """`CAMERA_LINE_SLOTS` greyed-out lines; slot n shows camera n, hidden when there is none."""

    def slot(number: int) -> MenuEntry:
        def text() -> str:
            current = lines()
            return current[number] if number < len(current) else ""

        return MenuEntry(text, None, visible=lambda: number < len(lines()))

    return [slot(number) for number in range(CAMERA_LINE_SLOTS)]
