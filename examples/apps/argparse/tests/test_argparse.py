"""PRD 14 §10 for the argparse example.

Rows covered here: T14-01, T14-02, T14-06, T14-07, T14-10, T14-14, plus the
startup/close ordering (T14-03) and the body-raises row (T14-05) as argparse
sees them. argparse is stdlib, so the guard names the distribution the app
actually depends on: ``lauren-di`` (``A-11``).
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from _testing import load

# No ``require()`` guard here on purpose: argparse is stdlib, so there is no
# distribution to look for, and ``lauren_di`` is a hard dependency of every
# example. A missing ``lauren_di`` is a broken checkout, not an absent framework.
argparse_app = load("argparse", "argparse_app")
argparse_di = load("argparse", "argparse_di")

APP_PATH = Path(argparse_app.__file__).resolve()
PROG = "argparse_app"

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
    argparse_app.EVENTS.clear()
    yield
    argparse_app.EVENTS.clear()


def _destruct_lines(text: str) -> list[str]:
    """Return the ``destruct_error:`` lines of ``text``, in order."""
    return [line for line in text.splitlines() if line.startswith("destruct_error:")]


def test_missing_command_exits_2_and_starts_no_container(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-01: ``main(["prog"])`` is a usage error, and nothing was started."""
    assert argparse_app.main([PROG]) == 2

    captured = capsys.readouterr()
    assert "required: command" in captured.err
    assert captured.out == ""
    assert argparse_app.EVENTS == []


def test_startup_runs_before_the_body_and_shutdown_after(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-03: the container starts after the arguments bind, and stops after."""
    assert argparse_app.main([PROG, "greet", "ada"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "hello ada (request ada)"
    assert argparse_app.EVENTS == [
        "mailer.started",
        "greeter.new",
        "greeter.closed",
        "mailer.stopped",
    ]


def test_scope_teardown_failure_exits_70_and_reports_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-02: exit 70, one ``destruct_error:`` on stderr, stdout stays clean."""
    assert argparse_app.main([PROG, "fragile"]) == 70

    captured = capsys.readouterr()
    assert captured.out.strip() == "hello fragile (request fragile)"
    assert "destruct_error:" not in captured.out
    lines = _destruct_lines(captured.err)
    assert len(lines) == 1
    assert "greeter refused to close" in lines[0]
    # "remaining destructors ran": the APP teardown still happened afterwards.
    assert argparse_app.EVENTS[-1] == "mailer.stopped"


def test_shutdown_failure_exits_70_and_reports_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-07: one ``DestructError`` from ``shutdown()`` is exit 70, not 0."""
    assert argparse_app.main([PROG, "stubborn"]) == 70

    captured = capsys.readouterr()
    assert captured.out.strip() == "stubborn"
    lines = _destruct_lines(captured.err)
    assert len(lines) == 1
    assert "mailer refused to close" in lines[0]


def test_body_exception_propagates_but_the_scope_still_closed() -> None:
    """T14-05, argparse side: the traceback reaches the user, teardown still ran."""
    with pytest.raises(RuntimeError, match="the command body failed"):
        argparse_app.main([PROG, "boom"])

    assert argparse_app.EVENTS == [
        "mailer.started",
        "greeter.new",
        "greeter.closed",
        "mailer.stopped",
    ]


def test_real_process_exits_1_when_the_body_raises() -> None:
    """T14-05: the real process reports rc 1, the same code the traceback earns."""
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
    """T14-06: Ctrl-C in the body runs the ``finally`` once and exits 130."""
    assert argparse_app.main([PROG, "interrupt"]) == argparse_di.EX_INTERRUPT

    assert argparse_app.EVENTS.count("greeter.closed") == 1
    assert argparse_app.EVENTS.count("mailer.stopped") == 1


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

    assert process.returncode == argparse_di.EX_TERMINATED
    assert "holding" in stdout
    assert "scope closed after SIGTERM" in stderr


def test_request_scoped_token_is_not_resolvable_on_the_container() -> None:
    """T14-10: the adapter resolves through the scope, never through the container."""
    from lauren_di import DIScopeViolationError

    host = argparse_di.CliHost(
        value_tokens=(argparse_app.REQUEST_ID,),
        providers=(argparse_app.Mailer, argparse_app.Greeter),
    )
    try:
        with pytest.raises(DIScopeViolationError):
            host.bridge.run(host.container.resolve(argparse_app.Greeter))
    finally:
        host.bridge.close()

    assert argparse_app.EVENTS == []


def test_two_invocations_run_two_lifecycles() -> None:
    """T14-03: the container is per invocation, so a second command starts a new one.

    The trace is the evidence: one ``mailer.started``/``mailer.stopped`` pair per
    command. A container kept between commands (or a module-level singleton)
    would show one pair and two command bodies.
    """
    assert argparse_app.main([PROG, "greet", "ada"]) == 0
    assert argparse_app.main([PROG, "greet", "grace"]) == 0

    assert argparse_app.EVENTS == LIFECYCLE_TWO


def test_the_real_process_reports_the_teardown_failure_before_it_exits() -> None:
    """T14-02: in a real process the scope closes, and its failure decides the code.

    ``main()`` returns 70 in-process (tested above); this case proves the same
    thing through ``sys.exit``, which is the only observation a shell has.
    """
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
