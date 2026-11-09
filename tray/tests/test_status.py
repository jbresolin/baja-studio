"""Tests for turning a `/api/health` answer into the badge and tooltip (status.py)."""

from baja_studio_tray.labels import EN, PT_BR
from baja_studio_tray.status import Badge, TrayStatus, status_from_health
from tests.fake_studio import health_answer


def test_an_ok_service_is_green_with_its_version() -> None:
    status = status_from_health(health_answer("ok", gateway="ok"), EN)

    assert status.badge == Badge.GREEN
    assert status.version == "0.2.0"
    assert status.tooltip(EN) == "Baja Studio 0.2.0: everything ok"


def test_a_degraded_service_is_amber_and_names_the_modules_with_problems() -> None:
    # Playout is offline and audio degraded; the gateway is fine and not named.
    health = health_answer("degraded", gateway="ok", playout="offline", audio="degraded")

    status = status_from_health(health, PT_BR)

    assert status.badge == Badge.AMBER
    assert status.summary == "atenção: 2 módulos com problema: Playout, Audio"


def test_one_module_with_a_problem_is_named_alone() -> None:
    status = status_from_health(health_answer("degraded", playout="offline"), EN)

    assert status.summary == "attention: Playout"


def test_no_answer_is_red() -> None:
    # None stands for "the service did not answer at all".
    status = status_from_health(None, PT_BR)

    assert status.badge == Badge.RED
    assert status.version is None
    assert status.tooltip(PT_BR) == "Baja Studio: o serviço não responde"


def test_a_status_the_tray_does_not_know_is_red() -> None:
    # A later service might answer "fail"; the tray must not show that as fine.
    status = status_from_health(health_answer("fail"), EN)

    assert status.badge == Badge.RED
    assert status.summary == "status fail"


def test_a_long_tooltip_is_cut_to_what_windows_shows() -> None:
    status = TrayStatus(Badge.AMBER, "0.2.0", "x" * 300)

    tooltip = status.tooltip(EN)

    # Windows keeps 127 characters; the cut is marked with an ellipsis.
    assert len(tooltip) == 127
    assert tooltip.endswith("…")
