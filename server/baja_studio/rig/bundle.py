"""The rig's settings bundle: an exported-settings file (ADR 0008) pointing at the fakes.

Written in the export format, so it goes in through the same import as any
other file (`POST /api/home/settings-import`, dry run, apply), or by hand on
Settings → Export / import when the rig runs with `--no-studio`. What it sets:

- agents: both fake agents with their token; cameras: each agent's two
  cameras, cam1 with a fake VISCA camera as its direct fallback;
- playout: the fake OBS, the fake orchestrator, the delivery folder as the
  receiver root and the "Patrocinadores" folder;
- audio: the fake XR18, every channel visible that the default link matrix
  links (PROD 3 and 4 included), seats 1-4 on channels 1-4 and buses 1-4,
  the production bus and microphones;
- hosts: the four seats with names and colours, all switched on;
- gateway: off (there is no Caddy on a desk PC).

Every key is checked against the settings models of this checkout; one the
models do not know is left out with a warning (and the rig's tests fail, so
a renamed setting is noticed here first). Its secrets are the fakes' own
throwaway values, written as they are so the import needs no typing.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from baja_studio import __version__
from baja_studio.audio import defaults as audio_defaults
from baja_studio.core.settings import ModuleSettings, nested_model_fields, settings_version_of
from baja_studio.core.settings_export import FILE_FORMAT, FILE_FORMAT_VERSION
from baja_studio.modules.agents import AgentsModule
from baja_studio.modules.audio import AudioModule
from baja_studio.modules.cameras import CamerasModule
from baja_studio.modules.gateway import GatewayModule
from baja_studio.modules.hosts import HostsModule
from baja_studio.modules.playout import PlayoutModule
from baja_studio.rig import fakes
from baja_studio.rig.clips import DELIVERY_FOLDER, SPONSORS_FOLDER, SPONSORS_LABEL
from baja_studio.rig.layout import LOOPBACK

BUNDLE_HOSTNAME = "baja-studio-rig"
# The sections the bundle writes, and the models that judge them.
SECTION_MODELS: dict[str, type[ModuleSettings]] = {
    module.name: module.SettingsModel
    for module in (
        AgentsModule,
        CamerasModule,
        PlayoutModule,
        AudioModule,
        HostsModule,
        GatewayModule,
    )
}
SEATS = (
    (1, "Ana", "#e74c3c"),
    (2, "Bruno", "#e67e22"),
    (3, "Carla", "#f1c40f"),
    (4, "Diego", "#2ecc71"),
)
PRODUCTION_BUS = "bus5"
PRODUCTION_CHANNELS = ["5", "6", "7", "8"]


@dataclass(frozen=True)
class Bundle:
    """The file's content, and the keys left out because the models do not know them."""

    document: dict[str, Any]
    warnings: list[str]

    def write(self, path: Path) -> None:
        """Save it as a settings file."""
        path.write_text(json.dumps(self.document, indent=2, ensure_ascii=False), encoding="utf-8")


def camera_id(agent_number: int, camera_number: int) -> str:
    """The rig's id for an agent camera, e.g. `rig-a-cam1`."""
    return f"rig-{'ab'[agent_number - 1]}-cam{camera_number}"


def section_values(ports: fakes.FakePorts, clips_folder: Path) -> dict[str, dict[str, Any]]:
    """Every section's values as the rig wants them, before any check."""
    agents = [
        {"id": agent_id, "host": LOOPBACK, "port": port, "token": fakes.AGENT_TOKEN}
        for agent_id, port in zip(fakes.AGENT_IDS, ports.agents, strict=True)
    ]
    cameras = []
    for agent_number, agent_id in enumerate(fakes.AGENT_IDS, start=1):
        for camera_number in range(1, fakes.CAMERAS_PER_AGENT + 1):
            camera: dict[str, Any] = {
                "id": camera_id(agent_number, camera_number),
                "label": f"Laptop {'AB'[agent_number - 1]} cam {camera_number}",
                "owner": "agent",
                "driver": "agent",
                "address": {"agent_id": agent_id, "remote_camera_id": f"cam{camera_number}"},
            }
            if camera_number == 1:
                fallback_port = ports.cameras[agent_number - 1]
                camera["direct_fallback"] = {
                    "driver": "visca_ip",
                    "address": {"host": LOOPBACK, "port": fallback_port},
                }
            cameras.append(camera)
    channels = audio_defaults.channels()
    for channel in PRODUCTION_CHANNELS:
        channels[channel]["visible"] = True
    return {
        "agents": {"agents": agents, "direct_fallback_after_s": 10.0},
        "cameras": {"cameras": cameras},
        "playout": {
            "sources": {
                "orchestrator": {
                    "enabled": True,
                    "base_url": f"http://{LOOPBACK}:{ports.orchestrator}",
                    "token": fakes.ORCHESTRATOR_TOKEN,
                    "poll_interval_s": 10.0,
                },
                "local_folders": [
                    {
                        "path": str(clips_folder / DELIVERY_FOLDER),
                        "label": "Entregas",
                        "receiver_root": True,
                    },
                    {"path": str(clips_folder / SPONSORS_FOLDER), "label": SPONSORS_LABEL},
                ],
            },
            "obs": {"host": LOOPBACK, "port": ports.obs, "password": fakes.OBS_PASSWORD},
        },
        "audio": {
            "mixer": {"host": LOOPBACK, "port": ports.xr18, "enabled": True},
            "channels": channels,
            "seats": [
                {"seat_id": seat_id, "channel": str(seat_id), "iem_bus": f"bus{seat_id}"}
                for seat_id, _, _ in SEATS
            ],
            "production_bus": PRODUCTION_BUS,
            "production_channels": PRODUCTION_CHANNELS,
        },
        "hosts": {
            "seats": [
                {"id": seat_id, "screen_name": name, "color": colour, "enabled": True}
                for seat_id, name, colour in SEATS
            ]
        },
        "gateway": {"enabled": False},
    }


def unknown_keys(model_class: type[BaseModel], values: dict[str, Any], prefix: str) -> list[str]:
    """Dotted paths in `values` the model (or a nested model) has no field for."""
    found = [f"{prefix}.{key}" for key in values if key not in model_class.model_fields]
    for name, (inner_class, is_list) in nested_model_fields(model_class).items():
        value = values.get(name)
        items = value if is_list and isinstance(value, list) else [value]
        for index, item in enumerate(items):
            if isinstance(item, dict):
                where = f"{prefix}.{name}[{index}]" if is_list else f"{prefix}.{name}"
                found.extend(unknown_keys(inner_class, item, where))
    return found


def without_keys(values: dict[str, Any], paths: list[str], prefix: str) -> dict[str, Any]:
    """A copy of `values` with the dotted `paths` (as `unknown_keys` names them) removed."""
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        here = f"{prefix}.{key}"
        if here in paths:
            continue
        if isinstance(value, dict):
            cleaned[key] = without_keys(value, paths, here)
        elif isinstance(value, list):
            cleaned[key] = [
                without_keys(item, paths, f"{here}[{index}]") if isinstance(item, dict) else item
                for index, item in enumerate(value)
            ]
        else:
            cleaned[key] = value
    return cleaned


def build_bundle(ports: fakes.FakePorts, clips_folder: Path) -> Bundle:
    """The settings file for these ports and clips, unknown keys left out with a warning."""
    modules: dict[str, Any] = {}
    warnings: list[str] = []
    for section, values in section_values(ports, clips_folder).items():
        model_class = SECTION_MODELS.get(section)
        if model_class is None:
            warnings.append(f"no settings section {section!r} in this studio; left out")
            continue
        unknown = unknown_keys(model_class, values, section)
        warnings.extend(f"{path} is not a setting of this studio; left out" for path in unknown)
        modules[section] = {
            "settings_version": settings_version_of(model_class),
            "values": without_keys(values, unknown, section),
        }
    document = {
        "format": FILE_FORMAT,
        "format_version": FILE_FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(),
        "hostname": BUNDLE_HOSTNAME,
        "secrets_required": [],
        "modules": modules,
    }
    return Bundle(document, warnings)
