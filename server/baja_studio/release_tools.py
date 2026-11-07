"""Helpers for installer/build.ps1 and the release workflow. Standard library only.

They run from `server/` with any Python 3.12, before the package's
dependencies exist anywhere:

    python -m baja_studio.release_tools stamp-version --tag v1.2.0
    python -m baja_studio.release_tools stamp-version --dev 57
    python -m baja_studio.release_tools version
    python -m baja_studio.release_tools requirements
    python -m baja_studio.release_tools wheelhouse-manifest --wheelhouse DIR --output FILE

`stamp-version` writes the version into `baja_studio/__init__.py` (the
package's version comes from there, see pyproject.toml) and prints it.
"""

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIRECTORY = Path(__file__).resolve().parent
INIT_FILE = PACKAGE_DIRECTORY / "__init__.py"
PYPROJECT_FILE = PACKAGE_DIRECTORY.parent / "pyproject.toml"
# v1.2.3, or a pre-release: v1.2.3-rc.1, v1.2.3-b2.
TAG_PATTERN = re.compile(r"^v(?P<release>\d+\.\d+\.\d+)(?:-(?P<kind>a|b|rc)\.?(?P<number>\d+))?$")
VERSION_LINE = re.compile(r'^__version__ = "(?P<version>[^"]*)"$', re.MULTILINE)


def version_from_tag(tag: str) -> str:
    """The Python version for a git tag: v1.2.3 → 1.2.3, v1.2.3-rc.1 → 1.2.3rc1."""
    match = TAG_PATTERN.match(tag)
    if match is None:
        raise ValueError(f"tag {tag!r} is not like v1.2.3 or v1.2.3-rc.1")
    if match["kind"] is None:
        return match["release"]
    return f"{match['release']}{match['kind']}{match['number']}"


def dev_version(current: str, build_number: int) -> str:
    """A build that is not a release (a pull request): 0.1.0 and build 57 → 0.1.0.dev57."""
    release = re.match(r"^\d+\.\d+\.\d+", current)
    if release is None:
        raise ValueError(f"current version {current!r} does not start with x.y.z")
    return f"{release.group(0)}.dev{build_number}"


def read_version(init_file: Path = INIT_FILE) -> str:
    """The version written in the package's `__init__.py`."""
    match = VERSION_LINE.search(init_file.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"no __version__ line in {init_file}")
    return match["version"]


def stamp_version(version: str, init_file: Path = INIT_FILE) -> None:
    """Replace the `__version__ = "..."` line; exactly one must exist."""
    text = init_file.read_text(encoding="utf-8")
    new_text, count = VERSION_LINE.subn(f'__version__ = "{version}"', text)
    if count != 1:
        raise ValueError(f"expected one __version__ line in {init_file}, found {count}")
    init_file.write_text(new_text, encoding="utf-8", newline="\n")


def runtime_requirements(pyproject: Path = PYPROJECT_FILE) -> list[str]:
    """The service's runtime dependencies, as pinned in pyproject.toml."""
    with pyproject.open("rb") as file:
        project = tomllib.load(file)["project"]
    return [str(requirement) for requirement in project.get("dependencies", [])]


def wheelhouse_manifest(wheelhouse: Path) -> list[dict[str, Any]]:
    """Every wheel with its size and SHA-256, sorted by name: what the install was built from."""
    entries = []
    for wheel in sorted(wheelhouse.glob("*.whl")):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        entries.append({"file": wheel.name, "bytes": wheel.stat().st_size, "sha256": digest})
    return entries


def main(arguments: list[str] | None = None) -> None:
    """The command line described at the top of this file."""
    parser = argparse.ArgumentParser(prog="baja_studio.release_tools")
    commands = parser.add_subparsers(dest="command", required=True)
    stamp = commands.add_parser("stamp-version", help="write the version into __init__.py")
    source = stamp.add_mutually_exclusive_group(required=True)
    source.add_argument("--tag", help="a release tag, e.g. v1.2.0")
    source.add_argument("--dev", type=int, help="a build number, for builds that are not releases")
    commands.add_parser("version", help="print the version written in __init__.py")
    commands.add_parser("requirements", help="print the runtime dependencies, one per line")
    manifest = commands.add_parser("wheelhouse-manifest", help="list the wheels with hashes")
    manifest.add_argument("--wheelhouse", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args(arguments)

    if parsed.command == "stamp-version":
        if parsed.tag:
            version = version_from_tag(parsed.tag)
        else:
            version = dev_version(read_version(), parsed.dev)
        stamp_version(version)
        print(version)
    elif parsed.command == "version":
        print(read_version())
    elif parsed.command == "requirements":
        print("\n".join(runtime_requirements()))
    else:
        entries = wheelhouse_manifest(parsed.wheelhouse)
        parsed.output.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        print(f"{len(entries)} wheels listed in {parsed.output}")


if __name__ == "__main__":
    main()
