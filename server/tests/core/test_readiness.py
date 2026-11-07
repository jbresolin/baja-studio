"""Tests for running readiness checks (core/readiness.py).

A readiness check is a small function a module declares ("is the XR18
answering?") that answers ok, warn or fail. `run_checks` runs many of them at
once, each on its own thread, and gives up on any that takes longer than 5 s.
These tests use made-up checks, so they are about the running, not the checking.
"""

import threading
from collections.abc import Callable, Iterator

import pytest

from baja_studio.core.readiness import (
    CRASHED_CHECK_FIX,
    CheckOutcome,
    CheckResult,
    CheckStatus,
    ReadinessCheck,
    failed,
    newly_failed,
    passed,
    run_checks,
    summarise,
    warning,
)
from tests.conftest import SAFETY_TIMEOUT_SECONDS, FakeClock, eventually


def check(check_id: str, run: Callable[[], CheckOutcome], fix: str = "do this") -> ReadinessCheck:
    """A check with a made-up label; only `id`, `run` and `fix` matter here."""
    return ReadinessCheck(check_id, f"label of {check_id}", run, fix)


@pytest.fixture
def release_hanging_checks() -> Iterator[threading.Event]:
    """An event hanging checks wait on; set at the end so their threads can finish."""
    release = threading.Event()
    yield release
    release.set()


def run_in_background(
    checks: list[tuple[str, ReadinessCheck]], fake_clock: FakeClock
) -> tuple[threading.Thread, list[CheckResult]]:
    """Start `run_checks` on another thread, because it waits on the fake clock."""
    results: list[CheckResult] = []
    runner = threading.Thread(
        target=lambda: results.extend(run_checks(checks, fake_clock)), daemon=True
    )
    runner.start()
    return runner, results


def test_results_come_back_in_the_order_the_checks_were_given(fake_clock: FakeClock) -> None:
    checks = [
        ("audio", check("mixer", lambda: passed("XR18 at 10.0.0.2"))),
        ("cameras", check("camera:a", lambda: warning("no frames"))),
        ("cameras", check("camera:b", lambda: failed("offline"))),
    ]

    results = run_checks(checks, fake_clock)

    assert [(r.module, r.id, r.status) for r in results] == [
        ("audio", "mixer", CheckStatus.OK),
        ("cameras", "camera:a", CheckStatus.WARN),
        ("cameras", "camera:b", CheckStatus.FAIL),
    ]
    assert results[0].detail == "XR18 at 10.0.0.2"
    assert results[0].key == "audio.mixer"


def test_checks_run_at_the_same_time(fake_clock: FakeClock) -> None:
    # Check "first" only finishes once check "second" has started. Run one
    # after the other, "first" would wait forever (and time out); run side by
    # side, both pass straight away.
    second_started = threading.Event()

    def first() -> CheckOutcome:
        assert second_started.wait(SAFETY_TIMEOUT_SECONDS)
        return passed()

    def second() -> CheckOutcome:
        second_started.set()
        return passed()

    results = run_checks([("m", check("first", first)), ("m", check("second", second))], fake_clock)

    assert [result.status for result in results] == [CheckStatus.OK, CheckStatus.OK]


def test_a_check_that_hangs_fails_with_timeout_after_five_seconds(
    fake_clock: FakeClock, release_hanging_checks: threading.Event
) -> None:
    # One check never answers (it waits for an event nobody sets); the other
    # answers at once. The runner waits on the fake clock, so time is moved by hand.
    def hangs() -> CheckOutcome:
        release_hanging_checks.wait()
        return passed()

    runner, results = run_in_background(
        [("playout", check("obs_scenes", hangs)), ("audio", check("mixer", passed))], fake_clock
    )
    eventually(lambda: fake_clock.sleeping_count == 1, "the runner never started waiting")

    # Just short of 5 s, the runner is still waiting.
    fake_clock.advance(4.9)
    assert runner.is_alive()
    fake_clock.advance(0.1)
    runner.join(SAFETY_TIMEOUT_SECONDS)

    hanging, quick = results
    assert hanging.status == CheckStatus.FAIL
    assert hanging.detail == "timeout: no answer within 5 s"
    assert hanging.fix == "do this"
    assert quick.status == CheckStatus.OK


def test_a_check_that_answers_after_its_timeout_keeps_the_timeout(
    fake_clock: FakeClock, release_hanging_checks: threading.Event
) -> None:
    answered = threading.Event()

    def slow() -> CheckOutcome:
        release_hanging_checks.wait()
        answered.set()
        return passed()

    runner, results = run_in_background([("m", check("slow", slow))], fake_clock)
    eventually(lambda: fake_clock.sleeping_count == 1)
    fake_clock.advance(5.0)
    runner.join(SAFETY_TIMEOUT_SECONDS)
    # Now let the slow check finish: the result already handed out must not change.
    release_hanging_checks.set()
    assert answered.wait(SAFETY_TIMEOUT_SECONDS)

    assert results[0].status == CheckStatus.FAIL


def test_a_check_that_raises_fails_and_says_it_is_a_bug(fake_clock: FakeClock) -> None:
    def broken() -> CheckOutcome:
        raise KeyError("channels")

    results = run_checks([("audio", check("links", broken))], fake_clock)

    assert results[0].status == CheckStatus.FAIL
    assert results[0].detail == "the check itself failed: KeyError: 'channels'"
    assert results[0].fix == CRASHED_CHECK_FIX


def test_a_passed_check_shows_no_fix_and_an_outcome_can_bring_a_better_one(
    fake_clock: FakeClock,
) -> None:
    # The check's own fix is generic; an outcome that knows the cause (the
    # program holding port 80) replaces it.
    checks = [
        ("gateway", check("caddy", passed, fix="start the gateway")),
        ("gateway", check("ports", lambda: failed("port 80 in use", "stop IIS"), fix="generic")),
        ("gateway", check("tls", lambda: failed("no certificate"), fix="paste a token")),
    ]

    results = run_checks(checks, fake_clock)

    assert [result.fix for result in results] == ["", "stop IIS", "paste a token"]


def test_each_result_is_reported_as_it_arrives(fake_clock: FakeClock) -> None:
    seen: list[str] = []

    run_checks(
        [("m", check("a", passed)), ("m", check("b", passed))],
        fake_clock,
        on_result=lambda result: seen.append(result.id),
    )

    assert sorted(seen) == ["a", "b"]


def test_no_checks_means_ready_at_once(fake_clock: FakeClock) -> None:
    assert run_checks([], fake_clock) == []
    assert summarise([]).banner == "READY"


def make_result(key: str, status: CheckStatus) -> CheckResult:
    module, _, check_id = key.partition(".")
    return CheckResult(module, check_id, check_id, status, "", "", None, 0.0)


def test_the_banner_counts_failures_and_warnings() -> None:
    results = [
        make_result("audio.mixer", CheckStatus.FAIL),
        make_result("audio.mvave", CheckStatus.WARN),
        make_result("cameras.camera:a", CheckStatus.WARN),
        make_result("home.version", CheckStatus.OK),
    ]

    summary = summarise(results)

    assert not summary.ready
    assert summary.banner == "NOT READY (1 fail, 2 warnings)"


def test_warnings_alone_still_mean_ready() -> None:
    summary = summarise([make_result("audio.mvave", CheckStatus.WARN)])

    assert summary.ready
    assert summary.banner == "READY"


def test_newly_failed_lists_only_checks_that_were_green_and_are_now_red() -> None:
    before = [
        make_result("audio.mixer", CheckStatus.OK),
        make_result("cameras.camera:a", CheckStatus.WARN),
        make_result("playout.obs", CheckStatus.FAIL),
    ]
    after = [
        make_result("audio.mixer", CheckStatus.FAIL),  # green → red: listed
        make_result("cameras.camera:a", CheckStatus.FAIL),  # amber → red: not
        make_result("playout.obs", CheckStatus.FAIL),  # red all along: not
        make_result("gateway.tls", CheckStatus.FAIL),  # new check: not
    ]

    assert newly_failed(before, after) == ["audio.mixer"]


def test_a_result_is_published_with_its_key_and_plain_status() -> None:
    result = CheckResult(
        "audio", "mixer", "XR18 answers", CheckStatus.FAIL, "offline", "x", None, 0.2
    )

    assert result.as_state() == {
        "module": "audio",
        "id": "mixer",
        "key": "audio.mixer",
        "label": "XR18 answers",
        "status": "fail",
        "detail": "offline",
        "fix": "x",
        "link": None,
        "duration_s": 0.2,
    }
