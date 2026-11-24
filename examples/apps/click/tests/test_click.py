"""PRD 14 §10 for the click example.

Rows covered here: T14-02, T14-03, T14-05, T14-06, T14-07, T14-10, T14-11, T14-14,
plus the usage-error half of T14-01 spelled for click. Every test is skip-guarded
through ``require``: an absent click must SKIP, never PASS (`D-62`).
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from _testing import load, require
from click.testing import CliRunner

require("click")

click_app = load("click", "click_app")
click_di = load("click", "click_di")

# One runner for the module. ``result.output`` mixes stdout and stderr in click
# 8.5, so every assertion below reads ``result.stdout`` or ``result.stderr``:
# which stream a line landed on is exactly what T14-02 and T14-07 are about.
RUNNER = CliRunner()

APP_PATH = Path(click_app.__file__).resolve()

#: What the ``hold`` command sleeps for before the test SIGTERMs it.
HOLD_SECONDS = 10.0
#: How long the test waits for the body to start before signalling.
STARTUP_GRACE = 1.5


#: Two complete invocations: ``started, built, closed, stopped`` twice (T14-03).
LIFECYCLE_TWO = [
    "mailer.started",
    "greeter.new",
    "greeter.closed",
    "mailer.stopped",
    "mailer.started",
    "greeter.new",
    "greeter.closed",
    "mailer.stopped",
]


@pytest.fixture(autouse=True)
def _reset_events() -> Iterator[None]:
    """Start and end every test with an empty lifecycle trace."""
    click_app.EVENTS.clear()
    yield
    click_app.EVENTS.clear()


def _destruct_lines(text: str) -> list[str]:
    """Return the ``destruct_error:`` lines of ``text``, in order."""
    return [line for line in text.splitlines() if line.startswith("destruct_error:")]


def test_startup_runs_before_the_body_and_shutdown_after() -> None:
    """T14-03: ``startup()`` precedes the body, ``shutdown()`` follows it."""
    result = RUNNER.invoke(click_app.app, ["greet", "ada"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello ada (request ada)"
    # The order of the trace *is* the assertion: started, built, closed, stopped.
    assert click_app.EVENTS == ["mailer.started", "greeter.new", "greeter.closed", "mailer.stopped"]


def test_scope_teardown_failure_exits_70_and_reports_on_stderr() -> None:
    """T14-02: the captured error goes to stderr, the command output stays clean."""
    result = RUNNER.invoke(click_app.app, ["fragile"])

    assert result.exit_code == 70
    assert result.stdout.strip() == "hello fragile (request fragile)"
    assert "destruct_error:" not in result.stdout
    assert len(_destruct_lines(result.stderr)) == 1
    # "remaining destructors ran": the APP teardown still happened afterwards.
    assert "fragile_greeter.closed" in click_app.EVENTS
    assert click_app.EVENTS[-1] == "mailer.stopped"


def test_shutdown_failure_exits_70_and_reports_on_stderr() -> None:
    """T14-07: one ``DestructError`` from ``shutdown()`` is exit 70, not 0."""
    result = RUNNER.invoke(click_app.app, ["stubborn"])

    assert result.exit_code == 70
    assert result.stdout.strip() == "stubborn"
    lines = _destruct_lines(result.stderr)
    assert len(lines) == 1
    assert "mailer refused to close" in lines[0]


def test_body_exception_reaches_click_and_the_scope_still_closed() -> None:
    """T14-05: the exception is not swallowed, and teardown still ran."""
    result = RUNNER.invoke(click_app.app, ["boom"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "the command body failed"
    assert click_app.EVENTS == ["mailer.started", "greeter.new", "greeter.closed", "mailer.stopped"]


def test_real_process_exits_1_when_the_body_raises() -> None:
    """T14-05: the real process reports rc 1, not click's usage code 2."""
    completed = subprocess.run(
        [sys.executable, str(APP_PATH), "boom"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1
    assert "the command body failed" in completed.stderr


def test_ctrl_c_closes_the_scope_exactly_once() -> None:
    """T14-06: Ctrl-C in the body runs the ``finally`` once and exits non-zero."""
    result = RUNNER.invoke(click_app.app, ["interrupt"])

    assert result.exit_code != 0
    assert click_app.EVENTS.count("greeter.closed") == 1
    assert click_app.EVENTS.count("mailer.stopped") == 1


def test_sigterm_closes_the_scope_before_the_process_exits() -> None:
    """T14-14: with a handler installed, SIGTERM unwinds instead of killing."""
    process = subprocess.Popen(
        [sys.executable, str(APP_PATH), "hold", str(HOLD_SECONDS)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(STARTUP_GRACE)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=60)
    finally:
        if process.poll() is None:  # pragma: no cover - a hung example is a bug
            process.kill()

    assert process.returncode == 143
    assert "holding" in stdout
    assert "scope closed after SIGTERM" in stderr


def test_usage_error_starts_no_container() -> None:
    """T14-01, click side: a missing argument is exit 2 and no ``startup()``."""
    result = RUNNER.invoke(click_app.app, ["greet"])

    assert result.exit_code == 2
    assert click_app.EVENTS == []


def test_request_scoped_token_is_not_resolvable_on_the_container() -> None:
    """T14-10: the adapter resolves through the scope, never through the container."""
    from lauren_di import DIScopeViolationError

    host = click_di.CliHost(
        value_tokens=(click_app.REQUEST_ID,),
        providers=(click_app.Mailer, click_app.Greeter),
    )
    try:
        with pytest.raises(DIScopeViolationError):
            host.bridge.run(host.container.resolve(click_app.Greeter))
    finally:
        host.bridge.close()

    assert click_app.EVENTS == []


def test_override_plan_replaces_the_mailer_under_the_runner() -> None:
    """T14-11: ``OverridePlan`` + ``building_with`` + ``CliRunner``, no new API."""
    from lauren_di import OverridePlan, ProviderOverride, building_with

    class FakeMailer:
        """A mailer the test owns, installed as the override's class."""

        def send(self, text: str) -> str:
            """Record the call instead of touching the real provider."""
            click_app.EVENTS.append("fake.sent")
            return text

    plan = OverridePlan(
        {click_app.Mailer: ProviderOverride(click_app.Mailer, "class", use_class=FakeMailer)}
    )
    with building_with(plan):
        result = RUNNER.invoke(click_app.app, ["greet", "ada"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello ada (request ada)"
    assert "fake.sent" in click_app.EVENTS
    assert "mailer.started" not in click_app.EVENTS


def test_two_invocations_run_two_lifecycles() -> None:
    """T14-03: a second command builds and closes a second container, not the first.

    Two full ``started``/``stopped`` pairs in the trace are the proof: a container
    kept between invocations would show one pair and two command bodies.
    """
    first = RUNNER.invoke(click_app.app, ["greet", "ada"])
    second = RUNNER.invoke(click_app.app, ["greet", "grace"])

    assert (first.exit_code, second.exit_code) == (0, 0)
    assert click_app.EVENTS == LIFECYCLE_TWO


def test_the_real_process_reports_the_teardown_failure_before_it_exits() -> None:
    """T14-02: through ``sys.exit`` the shell sees 70 and the ``destruct_error`` line."""
    completed = subprocess.run(
        [sys.executable, str(APP_PATH), "fragile"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 70
    assert completed.stdout.strip() == "hello fragile (request fragile)"
    assert "destruct_error:" in completed.stderr, "the teardown failure never reached the shell"
