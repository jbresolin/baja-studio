"""Lists drives and folders on the streaming PC for the folders form's "browse" button.

The page runs on another machine (or a phone), so a browser file dialog
would show the wrong disk; the server lists its own. Only folder names are
listed, never files, and a folder that cannot be read says so instead of
failing the page.
"""

import os
import string
import sys
from pathlib import Path
from typing import Any

MAX_LISTED = 500


def list_roots(platform: str = sys.platform) -> list[dict[str, str]]:
    """The places to start from: the drives that exist on Windows, `/` elsewhere."""
    if platform == "win32":
        drives = [f"{letter}:\\" for letter in string.ascii_uppercase]
        return [{"name": drive, "path": drive} for drive in drives if os.path.isdir(drive)]
    return [{"name": "/", "path": "/"}]


def browse(path: str, platform: str = sys.platform) -> dict[str, Any]:
    """Subfolders of `path` (the roots when `path` is empty), with the parent to go up to."""
    if not path.strip():
        return {"path": "", "parent": None, "folders": list_roots(platform), "error": None}
    folder = Path(path)
    parent = str(folder.parent) if folder.parent != folder else ""
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(folder)
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as error:
        return {"path": str(folder), "parent": parent, "folders": [], "error": str(error)}
    folders = [{"name": name, "path": str(folder / name)} for name in names[:MAX_LISTED]]
    return {"path": str(folder), "parent": parent, "folders": folders, "error": None}
