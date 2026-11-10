"""Tests for turning `/api/health` and the last readiness run into the badge and tooltip."""

from datetime import UTC, timedelta, timezone

import pytest

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


# The badge mapping with readiness (brief §2), one row per case: the health
# answer, the last readiness run's counts (None: no run yet), then the badge
# and summary expected. Times are given in UTC and shown in UTC here.
RUN_AT = "2026-11-10T14:32:05+00:00"
BADGE_TABLE = [
    ("ok", None, Badge.GREEN, "everything ok"),
    ("ok", {"fails": 0, "warnings": 0}, Badge.GREEN, "everything ok"),
    ("ok", {"fails": 0, "warnings": 1}, Badge.AMBER, "readiness at 14:32: 1 warning"),
    ("ok", {"fails": 0, "warnings": 3}, Badge.AMBER, "readiness at 14:32: 3 warnings"),
    ("ok", {"fails": 1, "warnings": 0}, Badge.AMBER, "readiness at 14:32: 1 failure"),
    (
        "ok",
        {"fails": 2, "warnings": 4},
        Badge.AMBER,
        "readiness at 14:32: 4 warnings, 2 failures",
    ),
    ("degraded", None, Badge.AMBER, "attention: Playout"),
    (
        "degraded",
        {"fails": 1, "warnings": 0},
        Badge.AMBER,
        "attention: Playout · readiness at 14:32: 1 failure",
    ),
    ("fail", {"fails": 1, "warnings": 0}, Badge.RED, "status fail"),
]


@pytest.mark.parametrize(("health_status", "counts", "badge", "summary"), BADGE_TABLE)
def test_the_badge_follows_health_and_the_last_readiness_run(
    health_status: str, counts: dict[str, int] | None, badge: Badge, summary: str
) -> None:
    last_run = None if counts is None else {"started_at": RUN_AT, **counts}

    status = status_from_health(health_answer(health_status, playout="offline"), EN, last_run, UTC)

    assert (status.badge, status.summary) == (badge, summary)


def test_a_service_that_does_not_answer_stays_red_whatever_readiness_said() -> None:
    status = status_from_health(None, EN, {"started_at": RUN_AT, "fails": 3}, UTC)

    assert status.badge == Badge.RED


def test_the_operators_see_the_counts_in_portuguese() -> None:
    last_run = {"started_at": RUN_AT, "fails": 1, "warnings": 2}

    status = status_from_health(health_answer("ok"), PT_BR, last_run, UTC)

    assert status.tooltip(PT_BR) == "Baja Studio 0.2.0: verificação das 14:32: 2 avisos, 1 falha"


def test_the_run_time_is_shown_in_the_pcs_time_zone() -> None:
    # The service gives UTC; São Paulo is three hours behind.
    sao_paulo = timezone(timedelta(hours=-3))

    status = status_from_health(
        health_answer("ok"), EN, {"started_at": RUN_AT, "warnings": 1}, sao_paulo
    )

    assert status.summary == "readiness at 11:32: 1 warning"


def test_an_unreadable_run_time_shows_a_question_mark() -> None:
    status = status_from_health(health_answer("ok"), EN, {"started_at": None, "warnings": 1}, UTC)

    assert status.summary == "readiness at ?: 1 warning"
