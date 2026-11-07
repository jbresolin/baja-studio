"""Tests for the release helpers (release_tools.py), used by installer/build.ps1.

They run on the build machine before anything is installed, so they use only
the standard library. Files are written to a temporary folder, never to the
real `__init__.py`.
"""

import json
from pathlib import Path

import pytest

from baja_studio import __version__
from baja_studio.release_tools import (
    dev_version,
    main,
    read_version,
    runtime_requirements,
    stamp_version,
    version_from_tag,
    wheelhouse_manifest,
)


@pytest.mark.parametrize(
    ("tag", "version"),
    [("v1.2.3", "1.2.3"), ("v1.2.3-rc.1", "1.2.3rc1"), ("v0.7.0-b2", "0.7.0b2")],
)
def test_release_tags_become_python_versions(tag: str, version: str) -> None:
    assert version_from_tag(tag) == version


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-beta", "v1.2.3 ", "release-1"])
def test_other_tags_are_refused(tag: str) -> None:
    with pytest.raises(ValueError, match=r"not like v1\.2\.3"):
        version_from_tag(tag)


def test_a_build_that_is_not_a_release_gets_a_dev_version() -> None:
    assert dev_version("0.1.0", 57) == "0.1.0.dev57"
    assert dev_version("1.2.3rc1", 8) == "1.2.3.dev8"


def test_stamping_replaces_only_the_version_line(tmp_path: Path) -> None:
    init_file = tmp_path / "__init__.py"
    init_file.write_text('"""Docs."""\n\n__version__ = "0.1.0"\n', encoding="utf-8")

    stamp_version("1.2.3", init_file)

    assert init_file.read_text(encoding="utf-8") == '"""Docs."""\n\n__version__ = "1.2.3"\n'
    assert read_version(init_file) == "1.2.3"


def test_stamping_a_file_without_a_version_line_is_an_error(tmp_path: Path) -> None:
    init_file = tmp_path / "__init__.py"
    init_file.write_text("VERSION = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="found 0"):
        stamp_version("1.2.3", init_file)


def test_the_real_package_has_a_version_line_to_stamp(capsys: pytest.CaptureFixture[str]) -> None:
    main(["version"])

    assert capsys.readouterr().out == f"{__version__}\n"
    assert read_version() == __version__


def test_the_runtime_requirements_are_the_pinned_dependencies() -> None:
    requirements = runtime_requirements()

    assert "fastapi==0.162.0" in requirements
    # The test tools are not shipped.
    assert not any(requirement.startswith("pytest") for requirement in requirements)
    assert all("==" in requirement for requirement in requirements)


def test_the_manifest_lists_each_wheel_with_its_hash(tmp_path: Path) -> None:
    (tmp_path / "b-1.0-py3-none-any.whl").write_bytes(b"bee")
    (tmp_path / "a-1.0-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not a wheel")
    output = tmp_path / "manifest.json"

    main(["wheelhouse-manifest", "--wheelhouse", str(tmp_path), "--output", str(output)])

    assert json.loads(output.read_text()) == wheelhouse_manifest(tmp_path)
    assert wheelhouse_manifest(tmp_path) == [
        {
            "file": "a-1.0-py3-none-any.whl",
            "bytes": 0,
            # The SHA-256 of no bytes at all.
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        },
        {
            "file": "b-1.0-py3-none-any.whl",
            "bytes": 3,
            "sha256": "62cb81b5904a262ffaeed02abef36bfc540b09f964b8b0b636662f77ffce6714",
        },
    ]
