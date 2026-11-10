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
    an unmatched delivery from a manual clip. A folder configured *inside*
    another one (`E:\\clips\\patrocinadores` under the receiver root
    `E:\\clips`) claims its own files: the most specific folder is walked
    first, so its label wins and its files are never "unmatched" (WP5c §1).
    """
    result = FolderScan()
    seen: set[str] = set()
    for folder in sorted(folders, key=_depth, reverse=True):
        root = Path(folder.path)
        if not root.is_dir():
            result.errors[folder.path] = "folder not found"
            continue
        label = folder.label or root.name
        in_receiver_root = folder == receiver_root
        try:
            for path in _video_files(root, folder.recursive):
                key = _comparable(path)
                if key in seen:
                    continue  # claimed by a more specific folder, or a folder listed twice
                seen.add(key)
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


def _depth(folder: LocalFolder) -> int:
    """How deep a folder is: deeper (more specific) folders are walked first.

    `sorted` keeps the settings' order between folders of the same depth.
    """
    return len(Path(folder.path).parts)


def _comparable(path: Path) -> str:
    """The same file written two ways (`E:/clips`, `e:\\clips\\`) compares equal on Windows."""
    return os.path.normcase(os.path.normpath(path))


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
