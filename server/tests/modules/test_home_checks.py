"""Tests for the home module's readiness checks (modules/home/checks.py): the PC itself.

The real module list runs with `system_probes` (tests/core/fake_system_probes.py)
standing in for the disk, NTP and the CPU and memory meters, so each test
says what the PC looks like and runs the home checks.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from baja_studio import __version__
from baja_studio.core.readiness import CheckResult, CheckStatus
from baja_studio.core.system_probes import DEFAULT_NTP_SERVER, GIGABYTE
from tests.conftest import AppFactory, FakeClock
from tests.core.fake_system_probes import FakeSystemProbes
from tests.modules.readiness_runner import assert_fix_given, run_module_checks, statuses


def home_results(make_app: AppFactory, fake_clock: FakeClock) -> dict[str, CheckResult]:
    app = make_app(None)
    with TestClient(app):
        return dict(run_module_checks(app.state.services, "home", fake_clock))


def test_a_healthy_pc_passes_every_check(
    make_app: AppFactory, fake_clock: FakeClock, data_directory: Path
) -> None:
    results = home_results(make_app, fake_clock)

    assert statuses(results) == {
        "version": CheckStatus.OK,
        "data_folder": CheckStatus.OK,
        "data_disk": CheckStatus.OK,
        "clock": CheckStatus.OK,
        "load": CheckStatus.OK,
    }
    details = {check_id: result.detail for check_id, result in results.items()}
    assert details["version"] == f"Baja Studio {__version__}"
    assert details["data_folder"] == str(data_directory)
    assert (
        details["data_disk"] == f"200.0 GB free on the drive of the data folder ({data_directory})"
    )
    assert details["clock"] == f"0.2 s behind {DEFAULT_NTP_SERVER}"
    assert details["load"] == "CPU 15%, memory 40%"


def test_a_nearly_full_data_drive_fails(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    system_probes.free_bytes = 12 * GIGABYTE

    result = home_results(make_app, fake_clock)["data_disk"]

    assert result.status == CheckStatus.FAIL
    assert_fix_given(result)


def test_a_clock_off_by_seconds_warns(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    system_probes.ntp_offset_seconds = -3.0
    seconds_off = home_results(make_app, fake_clock)["clock"]

    assert seconds_off.status == CheckStatus.WARN
    assert seconds_off.detail == f"3.0 s ahead of {DEFAULT_NTP_SERVER}"


def test_a_clock_off_by_minutes_fails(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    system_probes.ntp_offset_seconds = 600.0

    result = home_results(make_app, fake_clock)["clock"]

    assert result.status == CheckStatus.FAIL
    assert_fix_given(result)


def test_no_ntp_answer_skips_the_clock_check(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    # At the venue without internet: nothing can be said about the clock.
    system_probes.ntp_offset_seconds = None

    result = home_results(make_app, fake_clock)["clock"]

    assert result.status == CheckStatus.OK
    assert result.detail.startswith("not checked: no answer from")


def test_a_busy_cpu_only_warns(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    system_probes.cpu_percent = 97.0

    result = home_results(make_app, fake_clock)["load"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "CPU 97%, memory 40%"


def test_a_system_that_cannot_measure_load_passes(
    make_app: AppFactory, fake_clock: FakeClock, system_probes: FakeSystemProbes
) -> None:
    system_probes.cpu_percent = None
    system_probes.memory_percent = None

    result = home_results(make_app, fake_clock)["load"]

    assert (result.status, result.detail) == (
        CheckStatus.OK,
        "not measurable on this system",
    )
