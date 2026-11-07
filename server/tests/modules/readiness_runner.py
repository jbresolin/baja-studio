"""Runs one running module's readiness checks, for the tests of each module's checks.

The checks come from the module itself (`Module.readiness_checks()`) and run
the way the readiness module runs them (`run_checks`), so a test sees exactly
what the Readiness page would show for that module.
"""

from baja_studio.core.readiness import CheckResult, CheckStatus, run_checks
from baja_studio.core.services import StudioServices
from tests.conftest import FakeClock


def run_module_checks(
    services: StudioServices, module: str, clock: FakeClock
) -> dict[str, CheckResult]:
    """Each of `module`'s check results, by check id."""
    checks = services.registry.get(module).readiness_checks()
    results = run_checks([(module, check) for check in checks], clock)
    return {result.id: result for result in results}


def statuses(results: dict[str, CheckResult]) -> dict[str, CheckStatus]:
    """Just the status of each check, by id."""
    return {check_id: result.status for check_id, result in results.items()}


def assert_fix_given(result: CheckResult) -> None:
    """Every warning and failure must tell the operator what to do."""
    assert result.status != CheckStatus.OK
    assert result.fix.strip(), f"{result.key} has no fix"
