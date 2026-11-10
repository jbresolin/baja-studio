"""Tests for the hosts module's readiness checks (modules/hosts/readiness.py).

Run through `run_module_checks`, so each result is what the Readiness page
would show.
"""

from baja_studio.core.readiness import CheckStatus
from tests.modules.hosts_studio import HostsStudio
from tests.modules.readiness_runner import assert_fix_given, run_module_checks


def switch_seats(hosts_studio: HostsStudio, enabled: dict[int, bool]) -> None:
    for seat_id, on in enabled.items():
        hosts_studio.run("set_seat", {"seat_id": seat_id, "enabled": on})


def test_seats_pass_when_the_seats_in_use_have_names(hosts_studio: HostsStudio) -> None:
    result = run_module_checks(hosts_studio.services, "hosts", hosts_studio.clock)["seats"]

    assert result.status == CheckStatus.OK
    assert result.detail == "Host 1, Host 2"


def test_seats_fail_when_no_seat_is_switched_on(hosts_studio: HostsStudio) -> None:
    switch_seats(hosts_studio, {1: False, 2: False})

    results = run_module_checks(hosts_studio.services, "hosts", hosts_studio.clock)

    assert results["seats"].status == CheckStatus.FAIL
    assert_fix_given(results["seats"])
    assert results["seats"].link == "/hosts/control"
    # One cause, one red line: the screens check does not fail as well.
    assert results["screens"].status == CheckStatus.WARN
    assert results["screens"].detail == "not checked: no seat is switched on"


def test_screens_warn_when_no_hosts_screen_is_open(hosts_studio: HostsStudio) -> None:
    result = run_module_checks(hosts_studio.services, "hosts", hosts_studio.clock)["screens"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "no hosts screen is open"
    assert_fix_given(result)


def test_screens_warn_naming_a_seat_in_use_that_no_screen_shows(
    hosts_studio: HostsStudio,
) -> None:
    hosts_studio.run("register_screen", {"seats": [1]}, source="web:monitor")

    result = run_module_checks(hosts_studio.services, "hosts", hosts_studio.clock)["screens"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "no hosts screen shows Host 2"


def test_screens_pass_when_every_seat_in_use_is_on_a_screen(hosts_studio: HostsStudio) -> None:
    hosts_studio.run("register_screen", {"seats": [1, 2]}, source="web:monitor")

    result = run_module_checks(hosts_studio.services, "hosts", hosts_studio.clock)["screens"]

    assert result.status == CheckStatus.OK
