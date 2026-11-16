"""Which program a tray icon is for: Baja Studio, or the camera node on a trackside laptop.

One package serves both trays (ADR 0010 for the studio's, ADR 0023 for the
node's). What differs between them is gathered here, as plain data:

    pythonw -m baja_studio_tray          the studio's tray (the Baja artwork)
    pythonw -m baja_studio_tray --node   the camera node's tray (a camera)

The rest (polling, the badge dot, Quit, the menu's machinery) is shared.
"""

from dataclasses import dataclass
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class TrayProfile:
    """What one program's tray is called, looks like and where its files are."""

    # The name in the tooltip and on notifications ("Baja Studio 0.7.0: everything ok").
    product: str
    # pystray's internal icon name (no spaces).
    icon_name: str
    # The multi-size ICO the badge dot is drawn on.
    icon_path: Path
    # The badged copies are written as <prefix>-green.ico and so on, in a temporary folder.
    icon_file_prefix: str
    # Windows' named mutex that keeps one tray per logon session.
    single_instance_mutex: str
    # Where the installer records the program and data folders (under HKLM).
    registry_key: str
    # The data folder's name under ProgramData when the registry has none.
    default_data_folder: str


STUDIO = TrayProfile(
    product="Baja Studio",
    icon_name="BajaStudio",
    icon_path=ASSETS / "baja-studio.ico",
    icon_file_prefix="baja-studio",
    single_instance_mutex="Local\\BajaStudioTray",
    registry_key=r"Software\BajaStudio",
    default_data_folder="BajaStudio",
)

NODE = TrayProfile(
    product="Baja camera node",
    icon_name="BajaCameraNode",
    icon_path=ASSETS / "camera-node.ico",
    icon_file_prefix="camera-node",
    single_instance_mutex="Local\\BajaCameraNodeTray",
    registry_key=r"Software\BajaCameraNode",
    default_data_folder="BajaCameraNode",
)
