"""Tests for the folders form's "browse" listing (playout/browse.py).

Real folders in a temporary directory; the Windows drive list is checked
only for its shape, since this may run on Linux.
"""

from pathlib import Path

from baja_studio.playout.browse import browse, list_roots


def test_a_folder_lists_its_subfolders_only_sorted_without_hidden_ones(tmp_path: Path) -> None:
    for name in ("zeta", "Alpha", ".cache"):
        (tmp_path / name).mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"v")  # files are never listed

    listing = browse(str(tmp_path))

    assert [folder["name"] for folder in listing["folders"]] == ["Alpha", "zeta"]
    assert listing["folders"][0]["path"] == str(tmp_path / "Alpha")
    assert listing["parent"] == str(tmp_path.parent)
    assert listing["error"] is None


def test_an_unreadable_folder_says_why_instead_of_failing(tmp_path: Path) -> None:
    listing = browse(str(tmp_path / "unplugged"))

    assert listing["folders"] == []
    assert listing["error"] is not None
    # The operator can still go up from there.
    assert listing["parent"] == str(tmp_path)


def test_an_empty_path_lists_where_to_start() -> None:
    listing = browse("", platform="linux")

    assert listing["folders"] == [{"name": "/", "path": "/"}]
    assert listing["parent"] is None


def test_on_windows_the_roots_are_the_drives_that_exist() -> None:
    # On this test machine no "C:\\" exists unless it is Windows; either way
    # every listed root looks like a drive.
    for root in list_roots(platform="win32"):
        assert root["path"].endswith(":\\") and len(root["path"]) == 3


def test_the_top_of_a_drive_has_no_further_parent() -> None:
    listing = browse("/", platform="linux")

    assert listing["parent"] == ""
