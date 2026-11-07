"""Renders the Caddyfile from the gateway settings, and writes it where Caddy reads it.

The file lives in the data folder (`<data>/caddy/Caddyfile`), and the
`BajaStudioGateway` service starts Caddy with it, so Caddy comes up right
even before this service does. The Cloudflare token is never in it: the file
says `{env.CLOUDFLARE_API_TOKEN}` and Caddy reads the value from its own
environment, which NSSM sets (see controller.py).

The installer writes the defaults once, before the first start:

    python -m baja_studio.modules.gateway.caddyfile --data-dir <data folder> --if-missing
"""

import argparse
import os
from pathlib import Path

from baja_studio.modules.gateway.config import GatewaySettings, ProxiedApp, TlsMode

CADDY_DIRECTORY_NAME = "caddy"
CADDYFILE_NAME = "Caddyfile"
CERTIFICATE_STORAGE_NAME = "data"
TOKEN_ENVIRONMENT_VARIABLE = "CLOUDFLARE_API_TOKEN"
# How long a WebSocket (the studio's /ws) stays open after a reload replaced its route.
STREAM_CLOSE_DELAY = "5m"
HEADER = "# Written by Baja Studio from the gateway settings. Edits here are overwritten.\n"


def caddyfile_path(data_directory: Path) -> Path:
    """Where Caddy's config file lives: `<data>/caddy/Caddyfile`."""
    return data_directory / CADDY_DIRECTORY_NAME / CADDYFILE_NAME


def certificate_storage(data_directory: Path) -> Path:
    """Where Caddy keeps certificates and ACME accounts: `<data>/caddy/data`."""
    return data_directory / CADDY_DIRECTORY_NAME / CERTIFICATE_STORAGE_NAME


def render_caddyfile(settings: GatewaySettings, storage_directory: Path) -> str:
    """The whole Caddyfile for `settings`; certificates go to `storage_directory`."""
    storage = storage_directory.as_posix()
    if '"' in storage or "\n" in storage:
        raise ValueError(f"the data folder {storage!r} cannot be used in a Caddyfile")
    global_options = [
        f"admin {settings.admin_api}",
        # The Caddyfile is the only config; never resume from an autosaved copy.
        "persist_config off",
        f'storage file_system "{storage}"',
    ]
    if settings.tls.mode == TlsMode.OFF:
        global_options.append("auto_https off")
    blocks = [_block("", global_options)]
    routes = _routes(settings)
    if settings.tls.mode == TlsMode.DNS01:
        tls = _block("tls", [_block("dns cloudflare", [], f"{{env.{TOKEN_ENVIRONMENT_VARIABLE}}}")])
        blocks.append(_block(settings.hostname, [tls, *routes]))
        blocks.append(_block(":80", [f"redir https://{settings.hostname}{{uri}} 308"]))
    else:
        blocks.append(_block(":80", routes))
    return HEADER + "\n".join(blocks)


def _routes(settings: GatewaySettings) -> list[str]:
    """One `handle_path` per proxied app, then the studio for everything else."""
    routes: list[str] = []
    for app in settings.proxied_apps:
        routes.append(f"redir {app.path_prefix} {app.path_prefix}/ 308")
        routes.append(_block(f"handle_path {app.path_prefix}/*", [_reverse_proxy(app)]))
    # The studio's own page uses a WebSocket (/ws), so it always gets the delay.
    studio = _block(
        f"reverse_proxy {settings.studio_upstream}", [f"stream_close_delay {STREAM_CLOSE_DELAY}"]
    )
    routes.append(_block("handle", [studio]))
    return routes


def _reverse_proxy(app: ProxiedApp) -> str:
    if not app.websocket:
        return f"reverse_proxy {app.upstream}"
    return _block(f"reverse_proxy {app.upstream}", [f"stream_close_delay {STREAM_CLOSE_DELAY}"])


def _block(opening: str, lines: list[str], inline_argument: str = "") -> str:
    """`opening {` + indented lines + `}`; with no lines and an argument, one line."""
    if not lines and inline_argument:
        return f"{opening} {inline_argument}"
    body = "".join("\t" + line + "\n" for item in lines for line in item.splitlines() if item)
    head = f"{opening} {{" if opening else "{"
    return f"{head}\n{body}}}\n"


def write_caddyfile(path: Path, text: str) -> bool:
    """Write `text` atomically (Caddy may read at any moment); False if already identical."""
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return True


def write_default_caddyfile(data_directory: Path, only_if_missing: bool) -> Path:
    """Write the Caddyfile for default settings (fresh install); returns its path."""
    path = caddyfile_path(data_directory)
    if not (only_if_missing and path.is_file()):
        text = render_caddyfile(GatewaySettings(), certificate_storage(data_directory))
        write_caddyfile(path, text)
    return path


def main() -> None:
    """Command line used by the installer before it first starts the gateway service."""
    parser = argparse.ArgumentParser(description="Write the default gateway Caddyfile.")
    parser.add_argument("--data-dir", type=Path, required=True, help="the studio's data folder")
    parser.add_argument(
        "--if-missing", action="store_true", help="keep an existing Caddyfile (upgrades)"
    )
    arguments = parser.parse_args()
    print(write_default_caddyfile(arguments.data_dir, arguments.if_missing))


if __name__ == "__main__":
    main()
