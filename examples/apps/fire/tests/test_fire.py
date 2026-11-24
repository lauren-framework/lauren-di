"""PRD 14 §10 for the fire example.

Rows covered here: T14-02, T14-03, T14-06, T14-07, T14-10 and T14-14, plus the row
that is fire's alone: the printed value is built *after* the close point, so it
cannot describe a still-open scope. Fire has no test runner, so the app's own
``run`` helper is called in process.
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

require("fire")

fire_app = load("fire", "fire_app")
fire_di = load("fire", "fire_di")

APP_PATH = Path(fire_app.__file__).resolve()

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
    fire_app.EVENTS.clear()
    yield
    fire_app.EVENTS.clear()


def _destruct_lines(text: str) -> list[str]:
    """Return the ``destruct_error:`` lines of ``text``, in order."""
    return [line for line in text.splitlines() if line.startswith("destruct_error:")]


def test_fire_prints_a_value_built_after_the_close_point(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-03: startup, body, close, shutdown — and only then Fire's print."""
    fire_app.run(["greet", "ada"])

    captured = capsys.readouterr()
    assert "hello ada (request ada)" in captured.out
    # The printed dict carries the last lifecycle event, and that event is the
    # APP teardown: the scope and the container were closed before the print.
    assert "mailer.stopped" in captured.out
    assert fire_app.EVENTS == ["mailer.started", "greeter.new", "greeter.closed", "mailer.stopped"]


def test_scope_teardown_failure_exits_70_and_reports_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-02: exit 70, one ``destruct_error:`` on stderr, stdout stays clean."""
    with pytest.raises(SystemExit) as stop:
        fire_app.run(["fragile"])

    assert stop.value.code == fire_di.EX_TEARDOWN
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = _destruct_lines(captured.err)
    assert len(lines) == 1
    assert "greeter refused to close" in lines[0]
    assert fire_app.EVENTS[-1] == "mailer.stopped"


def test_shutdown_failure_exits_70_and_reports_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T14-07: one ``DestructError`` from ``shutdown()`` is exit 70, not 0."""
    with pytest.raises(SystemExit) as stop:
        fire_app.run(["stubborn"])

    assert stop.value.code == fire_di.EX_TEARDOWN
    lines = _destruct_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert "mailer refused to close" in lines[0]


def test_body_exception_propagates_but_the_scope_still_closed() -> None:
    """T14-05, fire side: Fire prints the traceback, the scope still closed."""
    with pytest.raises(RuntimeError, match="the command body failed"):
        fire_app.run(["boom"])

    assert fire_app.EVENTS == ["mailer.started", "greeter.new", "greeter.closed", "mailer.stopped"]


def test_ctrl_c_closes_the_scope_exactly_once() -> None:
    """T14-06: Ctrl-C in the body runs the ``finally`` once and exits 130."""
    with pytest.raises(SystemExit) as stop:
        fire_app.run(["interrupt"])

    assert stop.value.code == fire_di.EX_INTERRUPT
    assert fire_app.EVENTS.count("greeter.closed") == 1
    assert fire_app.EVENTS.count("mailer.stopped") == 1


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

    assert process.returncode == fire_di.EX_TERMINATED
    assert "holding" in stdout
    assert "scope closed after SIGTERM" in stderr


def test_request_scoped_token_is_not_resolvable_on_the_container() -> None:
    """T14-10: the adapter resolves through the scope, never through the container."""
    from lauren_di import DIScopeViolationError

    host = fire_di.CliHost(
        value_tokens=(fire_app.REQUEST_ID,),
        providers=(fire_app.Mailer, fire_app.Greeter),
    )
    try:
        with pytest.raises(DIScopeViolationError):
            host.bridge.run(host.container.resolve(fire_app.Greeter))
    finally:
        host.bridge.close()

    assert fire_app.EVENTS == []


def test_two_invocations_run_two_lifecycles() -> None:
    """T14-03: a second command builds and closes a second container, not the first.

    Two full ``started``/``stopped`` pairs in the trace are the proof: a container
    kept between invocations would show one pair and two command bodies.
    """
    fire_app.run(["greet", "ada"])
    fire_app.run(["greet", "grace"])

    assert fire_app.EVENTS == LIFECYCLE_TWO


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
    assert "destruct_error:" in completed.stderr, "the teardown failure never reached the shell"
