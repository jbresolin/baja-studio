"""Finds the video files in the watched folders (polled every 5 s by the module).

Plain directory walking, no `watchdog`: five-second polling is what the brief
asks for and it behaves the same on Windows shares and local disks. Dot-files
are skipped, which also skips the receiver's `.<name>.part` files while a
clip is still arriving.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from baja_studio.playout.config import LocalFolder
from baja_studio.playout.library import ScannedFile

SCAN_INTERVAL_SECONDS = 5.0
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mxf", ".ts", ".mts", ".wmv", ".flv"}
)


@dataclass
class FolderScan:
    """The result of one pass over every folder."""

    files: list[ScannedFile] = field(default_factory=list)
    # Folder path → why it could not be read (missing drive, no permission).
    errors: dict[str, str] = field(default_factory=dict)


def scan_folders(
    folders: list[LocalFolder], receiver_root: LocalFolder | None = None
) -> FolderScan:
    """Walk each folder and list its video files (subfolders too when `recursive`).

    Files found through `receiver_root` are flagged, so the library can tell
    an unmatched delivery from a manual clip.
    """
    result = FolderScan()
    seen: set[str] = set()
    for folder in folders:
        root = Path(folder.path)
        if not root.is_dir():
            result.errors[folder.path] = "folder not found"
            continue
        label = folder.label or root.name
        in_receiver_root = folder == receiver_root
        try:
            for path in _video_files(root, folder.recursive):
                if str(path) in seen:
                    continue  # a folder listed twice, or nested inside another
                seen.add(str(path))
                stat = path.stat()
                result.files.append(
                    ScannedFile(
                        path=str(path),
                        relative_path=path.relative_to(root).as_posix(),
                        folder_label=label,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        in_receiver_root=in_receiver_root,
                    )
                )
        except OSError as error:
            result.errors[folder.path] = str(error)
    return result


def _video_files(root: Path, recursive: bool) -> list[Path]:
    found = []
    for directory, subdirectories, names in os.walk(root):
        # Prune hidden folders in place so os.walk does not descend into them.
        subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
        for name in sorted(names):
            if not name.startswith(".") and Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                found.append(Path(directory) / name)
        if not recursive:
            break
    return found
