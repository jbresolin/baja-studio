"""A remote OBS's password never leaks (WP13, ADR 0021).

The password of a laptop's OBS is a secret like playout's OBS password: it
is masked in the settings API, replaced in an exported settings file,
scrubbed from the diagnostics bundle, and never in the shared state (which
goes to every open page), in a log line, in the command log or in a
command's answer. These tests run the whole service with the laptop's fake
OBS (crop_studio.py), use the password in every way the studio does
(connecting, a wrong one, Test connection), and then look for it everywhere.
"""

import json
import logging

import pytest

from baja_studio.core.settings_export import export_document
from tests.modules.crop_studio import LAPTOP_CAM_A, LAPTOP_PASSWORD, CropStudio
from tests.modules.test_cameras_remote_obs import all_ok, remote_camera, status_of
from tests.modules.test_cameras_remote_obs_setup import MASKED, obs_form

WRONG_PASSWORD = "not-the-laptop-password"


def use_the_password_every_way(studio: CropStudio) -> list[object]:
    """Connect with it, fail with a wrong one, test both; returns every command answer."""
    answers: list[object] = []
    studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A, password=WRONG_PASSWORD))
    studio.soon(lambda: status_of(studio, "lap-a") == "degraded")
    studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))
    studio.soon(lambda: all_ok(studio, "lap-a"), within_seconds=10.0)
    answers.append(studio.command("test_obs_connection", obs=obs_form()))
    answers.append(studio.command("test_obs_connection", obs=obs_form(WRONG_PASSWORD)))
    answers.append(studio.command("list_obs_sources", camera="lap-a", obs=obs_form(MASKED)))
    return answers


def test_the_password_is_not_in_the_state_nor_in_any_answer(crop_studio: CropStudio) -> None:
    answers = use_the_password_every_way(crop_studio)

    # The whole cameras state, as every page receives it.
    state = json.dumps(crop_studio.services.state.get("cameras"))
    for secret in (LAPTOP_PASSWORD, WRONG_PASSWORD):
        assert secret not in state
        assert secret not in json.dumps(answers)


def test_the_settings_api_masks_it_and_a_masked_save_keeps_it(crop_studio: CropStudio) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))

    # What Camera setup reads: the password shows as the mask.
    shown = crop_studio.http.get("/api/settings/cameras").json()
    assert shown["values"]["cameras"][0]["crop"]["obs"]["password"] == MASKED
    assert LAPTOP_PASSWORD not in json.dumps(shown)

    # The page saves the camera again (a label edit) with the mask: the password stays.
    camera = shown["values"]["cameras"][0]
    camera["label"] = "Laptop 1, cam A"
    saved = crop_studio.http.put(
        "/api/settings/cameras", json={"version": shown["version"], "values": shown["values"]}
    )
    assert saved.status_code == 200
    assert crop_studio.saved_camera().crop.obs.password == LAPTOP_PASSWORD


def test_an_exported_settings_file_leaves_it_out_and_lists_it(crop_studio: CropStudio) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))

    document = export_document(crop_studio.services.settings, crop_studio.clock, "studio-pc")

    assert LAPTOP_PASSWORD not in json.dumps(document)
    exported = document["modules"]["cameras"]["values"]["cameras"][0]["crop"]["obs"]
    assert exported["password"] == "<secret>"
    # The file says which secrets must be typed again after importing it.
    assert "cameras.cameras[lap-a].crop.obs.password" in document["secrets_required"]


def test_the_diagnostics_bundle_knows_to_scrub_it(crop_studio: CropStudio) -> None:
    crop_studio.save_cameras(remote_camera("lap-a", LAPTOP_CAM_A))

    # Every secret value the diagnostics bundle removes from the files it collects.
    assert LAPTOP_PASSWORD in crop_studio.services.settings.all_secret_values()


def test_no_log_line_holds_it(crop_studio: CropStudio, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        use_the_password_every_way(crop_studio)

    assert caplog.records, "nothing was logged at all: the test would prove nothing"
    for secret in (LAPTOP_PASSWORD, WRONG_PASSWORD):
        assert secret not in caplog.text
    # The command log (shown on the pages) does not have it either.
    tail = json.dumps(crop_studio.services.command_log.tail("cameras", 200))
    assert LAPTOP_PASSWORD not in tail
    assert WRONG_PASSWORD not in tail
