"""Reading a generated Ulanzi Studio profile back as Ulanzi Studio does: header, zip, manifests."""

import io
import json
import zipfile
from typing import Any

from baja_studio.audio.d200_profile import FORMAT_HEADER

BASE = "http://localhost:8888"


class Profile:
    """A generated profile, unpacked: its manifest and each page's buttons."""

    def __init__(self, data: bytes) -> None:
        assert data.startswith(FORMAT_HEADER)
        self.zip = zipfile.ZipFile(io.BytesIO(data[len(FORMAT_HEADER) :]))
        roots = {name.split("/")[0] for name in self.zip.namelist()}
        assert len(roots) == 1
        self.root = roots.pop()
        self.manifest: dict[str, Any] = self.read_json("manifest.json")
        self.page_ids: list[str] = self.manifest["Pages"]["Pages"]
        self.actions: list[dict[str, Any]] = [
            self.read_json(f"Profiles/{page}/manifest.json")["Controllers"][1]["Actions"]
            for page in self.page_ids
        ]

    def read_json(self, path: str) -> Any:
        return json.loads(self.zip.read(f"{self.root}/{path}"))

    def buttons(self, base: str = BASE) -> list[dict[str, dict[str, Any]]]:
        """Each page's buttons in the shape of d200_2026_layout.json."""
        return [
            {key: simple(action, base) for key, action in page.items()} for page in self.actions
        ]


def simple(action: dict[str, Any], base: str) -> dict[str, Any]:
    params = action["ActionParam"]
    kind = action["Action"].rsplit(".", 1)[-1]
    if kind == "goto":
        return {"goto": params.get("Page", 0)}
    if kind == "window":
        return {"screen": params["SmallViewMode"]}
    assert kind == "matchResult"
    return {
        "request": params["request_url"].removeprefix(base),
        "poll": params["poll_status_url"].removeprefix(base),
        "field": params["response_parse_field"],
        "on": image_name(params["image_matched"]),
        "off": image_name(params["image_unmatched"]),
    }


def image_name(path: str) -> str:
    """ "Files/HOST 1 - MUTE.png" → "HOST 1 - MUTE"."""
    return path.removeprefix("Files/").removesuffix(".png")
