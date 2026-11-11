"""The rig's studio: started on port 8889 with its own data folder, then set up over its API.

Setting it up uses only what the pages use, so the rig exercises them:

1. the settings import (upload, dry run, apply), as Settings → Export / import;
2. Audio setup → "Restore defaults…" for the macros and the link matrix
   (preview, save, restore), as the audio setup page sends them;
3. playout's "Return to" set to the scene OBS starts on, as on the playout page.

`StudioApi` takes any `httpx2.Client` aimed at the studio, so the tests hand it
Starlette's `TestClient` around an in-process studio.
"""

import time
from pathlib import Path
from typing import Any

import httpx2

from baja_studio.rig.fakes import STUDIO_ENVIRONMENT
from baja_studio.rig.layout import LOOPBACK
from baja_studio.rig.processes import RigProcess

STUDIO_PORT = 8889
CALLER = "rig"
# The scene the fake OBS has on program at start (`FakeObs.with_playout_scenes`).
RETURN_SCENE = "Main"
POLL_SECONDS = 0.25
HEALTH_TIMEOUT_SECONDS = 60.0
MIXER_TIMEOUT_SECONDS = 15.0
READINESS_TIMEOUT_SECONDS = 30.0


class StudioSetupError(Exception):
    """The studio refused a step of the setup; the message says which and why."""


def studio_process(
    data_directory: Path, log_directory: Path, port: int = STUDIO_PORT
) -> RigProcess:
    """The studio as a child of the rig, with the fake M-VAVE backend in its environment.

    `BAJA_STUDIO_DEBUG_LOGGERS`, when set, reaches it like the rest of the rig's environment.
    """
    arguments = ["--host", LOOPBACK, "--port", str(port), "--data-dir", str(data_directory)]
    return RigProcess(
        "studio", "baja_studio", arguments, log_directory, environment=STUDIO_ENVIRONMENT
    )


class StudioApi:
    """The setup steps, over the studio's HTTP API."""

    def __init__(self, client: httpx2.Client) -> None:
        """`client` has the studio as its base URL."""
        self._client = client

    def wait_until_up(self, timeout_s: float = HEALTH_TIMEOUT_SECONDS) -> None:
        """Wait for `/api/health` to answer."""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self._client.get("/api/health").status_code == 200:
                    return
            except httpx2.TransportError:
                pass
            if time.monotonic() > deadline:
                raise StudioSetupError("the studio did not start; see its log")
            time.sleep(POLL_SECONDS)

    def command(self, module: str, command: str, args: dict[str, Any] | None = None) -> Any:
        """Run a command as the rig (`api:rig` in the command log); its result."""
        response = self._client.post(
            "/api/commands",
            json={"module": module, "command": command, "args": args or {}, "caller": CALLER},
        )
        if response.status_code != 200:
            raise StudioSetupError(f"{module} {command}: {_detail(response)}")
        return response.json()["result"]

    def import_bundle(self, content: bytes) -> list[dict[str, str]]:
        """Upload, dry run and apply a settings file; the per-section results."""
        upload = self._client.post("/api/home/settings-import", content=content)
        if upload.status_code != 200:
            raise StudioSetupError(f"settings upload: {_detail(upload)}")
        upload_id = upload.json()["upload_id"]
        plan = self.command("home", "import_settings_dryrun", {"upload_id": upload_id})
        applied = self.command(
            "home",
            "import_settings_apply",
            {"upload_id": upload_id, "token": plan["token"], "apply_partial": False},
        )
        results: list[dict[str, str]] = applied["results"]
        refused = [item for item in results if item["result"].startswith("skipped")]
        if refused:
            raise StudioSetupError(
                "; ".join(f"{item['section']}: {item['result']}" for item in refused)
            )
        return results

    def restore_audio_defaults(self) -> list[str]:
        """Audio setup → Restore defaults… for the macros, then the link matrix; what changed."""
        done = []
        current = self._client.get("/api/settings/audio").json()
        preview = self.command(
            "audio", "preview_defaults", {"part": "macros", "macros": current["values"]["macros"]}
        )
        if any(change["change"] in ("replaced", "added") for change in preview["changes"]):
            saved = self._client.put(
                "/api/settings/audio",
                json={"version": current["version"], "values": {"macros": preview["macros"]}},
            )
            if saved.status_code != 200:
                raise StudioSetupError(f"saving the default macros: {_detail(saved)}")
            self.command(
                "audio",
                "restore_defaults",
                {
                    "part": "macros",
                    "version": saved.json()["version"],
                    "changes": preview["changes"],
                },
            )
            done.append("default macros")
        links = self.command("audio", "preview_defaults", {"part": "links"})
        if links["changes"]:
            self.command(
                "audio", "restore_defaults", {"part": "links", "expected": links["current"]}
            )
            done.append(f"link matrix ({len(links['changes'])} changes)")
        return done

    def set_return_scene(self, scene: str = RETURN_SCENE) -> None:
        """Playout's "Return to: chosen scene" (session state, so not in the bundle)."""
        self.command("playout", "set_return_target", {"mode": "chosen", "scene": scene})

    def wait_for_module(self, name: str, timeout_s: float = MIXER_TIMEOUT_SECONDS) -> bool:
        """Wait for one module's health to be `ok`; False if it is not by the deadline."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            modules = self._client.get("/api/health").json()["modules"]
            if any(module["name"] == name and module["state"] == "ok" for module in modules):
                return True
            time.sleep(POLL_SECONDS)
        return False

    def run_readiness(self, timeout_s: float = READINESS_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Run every readiness check now and return the finished run (`last_run`)."""
        before = self._last_run_number()
        self.command("readiness", "run")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            last = self._client.get("/api/readiness/last").json()["last_run"]
            if last is not None and last["number"] != before:
                return dict(last)
            time.sleep(POLL_SECONDS)
        raise StudioSetupError("the readiness run did not finish")

    def _last_run_number(self) -> int | None:
        last = self._client.get("/api/readiness/last").json()["last_run"]
        return None if last is None else int(last["number"])


def not_ok_checks(last_run: dict[str, Any]) -> list[str]:
    """Every check of a readiness run that is not `ok`, as "module/check: status detail"."""
    return [
        f"{module['name']}/{check['id']}: {check['status']} {check['detail']}"
        for module in last_run["modules"]
        for check in module["checks"]
        if check["status"] != "ok"
    ]


def _detail(response: httpx2.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return f"HTTP {response.status_code}"
