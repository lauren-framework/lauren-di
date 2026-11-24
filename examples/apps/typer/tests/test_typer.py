"""PRD 14 §10 for the Typer example.

Rows covered here: T14-02, T14-03, T14-04, T14-06, T14-07, T14-08, T14-10, T14-14
and T14-15. Typer has no runner of its own for a bare ``Typer`` object, so every
invocation goes through ``typer.testing.CliRunner``.
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
from typer.testing import CliRunner

require("typer")

typer_app = load("typer", "typer_app")
typer_di = load("typer", "typer_di")

RUNNER = CliRunner()
APP_PATH = Path(typer_app.__file__).resolve()

#: What the ``hold`` mode sleeps for before the test SIGTERMs it.
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
    typer_app.EVENTS.clear()
    yield
    typer_app.EVENTS.clear()


def _destruct_lines(text: str) -> list[str]:
    """Return the ``destruct_error:`` lines of ``text``, in order."""
    return [line for line in text.splitlines() if line.startswith("destruct_error:")]


def test_one_command_app_runs_the_body_on_empty_argv() -> None:
    """T14-15: ``invoke(app, [])`` runs the single command and exits 0."""
    result = RUNNER.invoke(typer_app.app, [])

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello world (request world)"
    assert typer_app.EVENTS == ["mailer.started", "greeter.new", "greeter.closed", "mailer.stopped"]


def test_startup_runs_before_the_body_and_shutdown_after() -> None:
    """T14-03: ``startup()`` precedes the body, ``shutdown()`` follows it."""
    result = RUNNER.invoke(typer_app.app, ["ada"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello ada (request ada)"


def test_scope_teardown_failure_exits_70_and_reports_on_stderr() -> None:
    """T14-02: the captured error goes to stderr, the command output stays clean."""
    result = RUNNER.invoke(typer_app.app, ["--mode", "fragile"])

    assert result.exit_code == 70
    assert result.stdout.strip() == "hello world (request world)"
    assert "destruct_error:" not in result.stdout
    assert len(_destruct_lines(result.stderr)) == 1
    assert typer_app.EVENTS[-1] == "mailer.stopped"


def test_shutdown_failure_exits_70_and_reports_on_stderr() -> None:
    """T14-07: one ``DestructError`` from ``shutdown()`` is exit 70, not 0."""
    result = RUNNER.invoke(typer_app.app, ["--mode", "stubborn"])

    assert result.exit_code == 70
    lines = _destruct_lines(result.stderr)
    assert len(lines) == 1
    assert "mailer refused to close" in lines[0]


def test_async_command_never_runs_the_body() -> None:
    """T14-04: Typer drops an ``async def`` command's coroutine, and exits 0."""
    result = RUNNER.invoke(typer_app.build_async_app(), [])

    assert result.exit_code == 0
    assert typer_app.EVENTS == []


def test_annotated_injection_is_rejected_before_the_body() -> None:
    """T14-08: an injected parameter is a CLI parameter Typer cannot convert.

    PRD 14 records this as a usage error — exit 2 with ``{mailer}`` in the usage
    line — and that is what the pinned Typer does: the annotation is bound as a
    positional argument, so the invocation fails before the body runs and before
    any container exists. The recipe never declares injections as parameters,
    which is what G4 asserts.
    """
    result = RUNNER.invoke(typer_app.build_annotated_app(), [])

    assert result.exit_code == 2
    assert "{mailer}" in result.output, (
        f"the usage line no longer names the injected parameter:\n{result.output}"
    )
    assert "Missing argument 'mailer'" in result.output
    assert typer_app.EVENTS == []


def test_ctrl_c_closes_the_scope_exactly_once() -> None:
    """T14-06: Ctrl-C in the body runs the ``finally`` once and exits non-zero."""
    result = RUNNER.invoke(typer_app.app, ["--mode", "interrupt"])

    assert result.exit_code != 0
    assert typer_app.EVENTS.count("greeter.closed") == 1
    assert typer_app.EVENTS.count("mailer.stopped") == 1


def test_sigterm_closes_the_scope_before_the_process_exits() -> None:
    """T14-14: with a handler installed, SIGTERM unwinds instead of killing."""
    process = subprocess.Popen(
        [sys.executable, str(APP_PATH), "--hold", str(HOLD_SECONDS)],
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

    assert process.returncode == typer_di.EX_TERMINATED
    assert "holding" in stdout
    assert "scope closed after SIGTERM" in stderr


def test_request_scoped_token_is_not_resolvable_on_the_container() -> None:
    """T14-10: the adapter resolves through the scope, never through the container."""
    from lauren_di import DIScopeViolationError

    host = typer_di.CliHost(
        value_tokens=(typer_app.REQUEST_ID,),
        providers=(typer_app.Mailer, typer_app.Greeter),
    )
    try:
        with pytest.raises(DIScopeViolationError):
            host.bridge.run(host.container.resolve(typer_app.Greeter))
    finally:
        host.bridge.close()

    assert typer_app.EVENTS == []


def test_two_invocations_run_two_lifecycles() -> None:
    """T14-03: a second command builds and closes a second container, not the first.

    Two full ``started``/``stopped`` pairs in the trace are the proof: a container
    kept between invocations would show one pair and two command bodies.
    """
    first = RUNNER.invoke(typer_app.app, ["ada"])
    second = RUNNER.invoke(typer_app.app, ["grace"])

    assert (first.exit_code, second.exit_code) == (0, 0)
    assert typer_app.EVENTS == LIFECYCLE_TWO


def test_the_real_process_reports_the_teardown_failure_before_it_exits() -> None:
    """T14-02: through ``sys.exit`` the shell sees 70 and the ``destruct_error`` line."""
    completed = subprocess.run(
        [sys.executable, str(APP_PATH), "--mode", "fragile"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 70
    assert "destruct_error:" in completed.stderr, "the teardown failure never reached the shell"
