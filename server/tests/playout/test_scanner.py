"""Tests for the folder scanner (playout/scanner.py).

Each test builds a small folder tree in pytest's `tmp_path` (a fresh empty
folder per test) and scans it.
"""

from pathlib import Path

from baja_studio.playout.config import LocalFolder
from baja_studio.playout.scanner import scan_folders


def make_file(path: Path, content: bytes = b"video") -> Path:
    """Create a file (and its parent folders) with some bytes in it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_a_recursive_folder_lists_videos_in_subfolders_with_posix_relative_paths(
    tmp_path: Path,
) -> None:
    # The receiver's layout: <prova>/<car>/<passagem>.mp4 under the clip root.
    root = tmp_path / "clips"
    make_file(root / "enduro" / "42" / "3.mp4", b"12345")
    make_file(root / "intro.MOV")

    scan = scan_folders([LocalFolder(path=str(root), label="Delivery")])

    # Both files are found; relative paths use "/" on every OS, because they
    # are compared with the orchestrator's `relative_path`.
    found = {file.relative_path: file for file in scan.files}
    assert set(found) == {"enduro/42/3.mp4", "intro.MOV"}
    assert found["enduro/42/3.mp4"].size == 5
    assert found["enduro/42/3.mp4"].folder_label == "Delivery"
    assert found["enduro/42/3.mp4"].path == str(root / "enduro" / "42" / "3.mp4")
    assert scan.errors == {}


def test_a_non_recursive_folder_ignores_subfolders(tmp_path: Path) -> None:
    root = tmp_path / "sponsors"
    make_file(root / "top.mp4")
    make_file(root / "old" / "below.mp4")

    scan = scan_folders([LocalFolder(path=str(root), recursive=False)])

    # Only the file directly in the folder; the label defaults to the folder name.
    assert [file.relative_path for file in scan.files] == ["top.mp4"]
    assert scan.files[0].folder_label == "sponsors"


def test_hidden_files_partial_files_and_non_videos_are_skipped(tmp_path: Path) -> None:
    # The receiver writes ".<name>.part" while a clip arrives; it must not be
    # offered for playout until it is renamed. Hidden folders are skipped too.
    root = tmp_path / "clips"
    make_file(root / "enduro" / "42" / ".3.mp4.part")
    make_file(root / ".hidden.mp4")
    make_file(root / ".cache" / "inside.mp4")
    make_file(root / "latest.m3u")
    make_file(root / "notes.txt")
    make_file(root / "real.mp4")

    scan = scan_folders([LocalFolder(path=str(root))])

    assert [file.relative_path for file in scan.files] == ["real.mp4"]


def test_a_missing_folder_is_reported_and_the_others_still_scanned(tmp_path: Path) -> None:
    # The delivery drive is unplugged, but the sponsor folder is fine.
    good = tmp_path / "good"
    make_file(good / "a.mp4")
    missing = tmp_path / "not-there"

    scan = scan_folders([LocalFolder(path=str(missing)), LocalFolder(path=str(good))])

    assert scan.errors == {str(missing): "folder not found"}
    assert [file.relative_path for file in scan.files] == ["a.mp4"]


def test_a_folder_listed_twice_does_not_list_its_files_twice(tmp_path: Path) -> None:
    # A nested folder added on its own as well as through its parent.
    root = tmp_path / "clips"
    make_file(root / "sub" / "a.mp4")

    scan = scan_folders([LocalFolder(path=str(root)), LocalFolder(path=str(root / "sub"))])

    assert len(scan.files) == 1


def test_files_found_through_the_receiver_root_are_flagged(tmp_path: Path) -> None:
    # The library calls a file from the receiver root with no manifest entry
    # "unmatched", and a file from any other folder "manual".
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "old.mp4").write_bytes(b"v")
    (tmp_path / "inst").mkdir()
    (tmp_path / "inst" / "sae.mp4").write_bytes(b"v")
    root = LocalFolder(path=str(tmp_path / "root"), receiver_root=True)
    other = LocalFolder(path=str(tmp_path / "inst"), label="SAE institucional")

    scan = scan_folders([root, other], root)

    flags = {Path(found.path).name: found.in_receiver_root for found in scan.files}
    assert flags == {"old.mp4": True, "sae.mp4": False}
