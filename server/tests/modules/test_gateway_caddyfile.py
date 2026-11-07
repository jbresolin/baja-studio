"""Tests for the Caddyfile renderer (modules/gateway/caddyfile.py).

Each combination of settings is compared with a "golden file" in
`tests/modules/gateway_golden/`: the exact Caddyfile we expect, kept in the
repository so a reviewer can read what Caddy will run. When a change to the
renderer is intended, regenerate them with

    UPDATE_GOLDEN=1 pytest tests/modules/test_gateway_caddyfile.py

and read the diff before committing it.
"""

import os
from pathlib import Path, PurePosixPath

import pytest

from baja_studio.modules.gateway.caddyfile import (
    caddyfile_path,
    certificate_storage,
    render_caddyfile,
    write_caddyfile,
    write_default_caddyfile,
)
from baja_studio.modules.gateway.config import GatewaySettings

GOLDEN_DIRECTORY = Path(__file__).parent / "gateway_golden"
# A fixed storage folder, so the files are the same on every machine.
STORAGE = Path(PurePosixPath("/data/BajaStudio/caddy/data"))
TOKEN = "cf-token-0123456789abcdef"

OVERLAY = {"path_prefix": "/overlay", "upstream": "127.0.0.1:5000", "label": "BajaOvrLay"}
REVIEW = {
    "path_prefix": "/review",
    "upstream": "10.0.0.7:8080",
    "websocket": True,
    "label": "Pipeline review",
}

CASES: dict[str, dict[str, object]] = {
    # The defaults: TLS off, so plain HTTP on port 80 to the studio.
    "tls-off-defaults": {},
    "tls-off-with-apps": {"proxied_apps": [OVERLAY, REVIEW]},
    "dns01-studio-only": {"tls": {"mode": "dns01", "cloudflare_api_token": TOKEN}},
    "dns01-with-apps": {
        "tls": {"mode": "dns01", "cloudflare_api_token": TOKEN},
        "proxied_apps": [OVERLAY, REVIEW],
    },
    # A token is not needed to render: Caddy reports the missing token itself.
    "dns01-without-token": {"tls": {"mode": "dns01"}},
    "custom-addresses": {
        "hostname": "studio.example.net",
        "tls": {"mode": "dns01", "cloudflare_api_token": TOKEN},
        "studio_upstream": "127.0.0.1:9999",
        "admin_api": "localhost:2020",
    },
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_settings_combination_renders_its_golden_file(case: str) -> None:
    settings = GatewaySettings.model_validate(CASES[case])

    rendered = render_caddyfile(settings, STORAGE)

    golden = GOLDEN_DIRECTORY / f"{case}.Caddyfile"
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(rendered, encoding="utf-8", newline="\n")
    assert rendered == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_token_is_never_in_the_caddyfile(case: str) -> None:
    # Caddy reads the token from its environment; the file only names the variable.
    rendered = render_caddyfile(GatewaySettings.model_validate(CASES[case]), STORAGE)

    assert TOKEN not in rendered
    assert "cf-token" not in rendered


def test_with_tls_on_the_token_comes_from_caddys_environment() -> None:
    settings = GatewaySettings.model_validate(CASES["dns01-studio-only"])

    rendered = render_caddyfile(settings, STORAGE)

    assert "dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in rendered


def test_every_golden_file_is_still_used() -> None:
    # A renamed case would otherwise leave a stale file nobody checks.
    files = {path.stem for path in GOLDEN_DIRECTORY.glob("*.Caddyfile")}

    assert files == set(CASES)


def test_a_data_folder_with_a_quote_is_refused_rather_than_written_into_the_file() -> None:
    with pytest.raises(ValueError, match="cannot be used"):
        render_caddyfile(GatewaySettings(), Path('/data/"odd"/caddy'))


def test_the_caddyfile_and_certificates_live_in_the_data_folder(tmp_path: Path) -> None:
    assert caddyfile_path(tmp_path) == tmp_path / "caddy" / "Caddyfile"
    assert certificate_storage(tmp_path) == tmp_path / "caddy" / "data"


def test_writing_the_same_caddyfile_twice_reports_no_change(tmp_path: Path) -> None:
    path = tmp_path / "caddy" / "Caddyfile"

    first = write_caddyfile(path, "a\n")
    second = write_caddyfile(path, "a\n")
    third = write_caddyfile(path, "b\n")

    assert (first, second, third) == (True, False, True)
    assert path.read_text(encoding="utf-8") == "b\n"
    # The temporary file used for the atomic swap is gone.
    assert sorted(item.name for item in path.parent.iterdir()) == ["Caddyfile"]


def test_the_installer_default_matches_the_default_settings(tmp_path: Path) -> None:
    # The installer writes this before Caddy's first start; the service later
    # writes the same thing from its (default) settings, so nothing changes.
    path = write_default_caddyfile(tmp_path, only_if_missing=True)

    expected = render_caddyfile(GatewaySettings(), certificate_storage(tmp_path))
    assert path.read_text(encoding="utf-8") == expected


def test_an_upgrade_keeps_the_caddyfile_the_service_wrote(tmp_path: Path) -> None:
    # On an upgrade the file holds the operator's config (TLS on, apps); keep it.
    path = caddyfile_path(tmp_path)
    write_caddyfile(path, "# the operator's config\n")

    write_default_caddyfile(tmp_path, only_if_missing=True)

    assert path.read_text(encoding="utf-8") == "# the operator's config\n"
