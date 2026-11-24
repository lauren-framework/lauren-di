"""The cyclopts adapter: one container per process, one unit per invocation.

PRD 14 §5.7 and PRD 09 §5.1: cyclopts *does* drive ``async def`` commands and
takes their return value as the process exit code, so the adapter must not hand
the loop to cyclopts. Every container call stays on this module's single
long-lived loop, and cyclopts still owns the process's exit status.
"""

from __future__ import annotations

import contextlib
import pathlib
import signal
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from types import FrameType
from typing import Any

# Running this app as a script puts only *this* directory on ``sys.path``, so
# neither ``_host`` (one level up) nor ``lauren_di`` (the checkout root, when the
# example runs uninstalled) is importable. Appending keeps site-packages first,
# exactly like ``examples/apps/conftest.py``.
_APPS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_APPS_DIR) not in sys.path:
    sys.path.append(str(_APPS_DIR))
_CHECKOUT = next((parent for parent in _APPS_DIR.parents if (parent / "lauren_di").is_dir()), None)
if _CHECKOUT is not None and str(_CHECKOUT) not in sys.path:
    sys.path.append(str(_CHECKOUT))

from _host import HostUnit, SyncBridge, build_container  # noqa: E402
from lauren_di import DestructError  # noqa: E402

#: ``sysexits.h`` EX_SOFTWARE: the container's teardown contract was broken.
EX_TEARDOWN = 70
#: What a shell reports for a Ctrl-C (``128 + SIGINT``).
EX_INTERRUPT = 130
#: What a shell reports for SIGTERM (``128 + 15``).
EX_TERMINATED = 143


class Terminated(Exception):
    """The process was asked to stop by ``SIGTERM``.

    A handler that calls ``sys.exit`` straight from the signal would skip every
    ``finally`` between the sleep and the interpreter. Raising instead unwinds
    them first, so the scope still closes before the process dies (A-10).
    """


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise Terminated(signum)


def install_sigterm_handler() -> None:
    """Turn ``SIGTERM`` into :class:`Terminated` so the close point still runs.

    ``signal.signal`` only works in the main thread; a test that runs the app on
    a worker thread gets the default disposition and says so by not calling this.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    # Respect a handler somebody else already installed (a supervisor, pytest, a
    # wrapper script): the example owns the default disposition, not theirs.
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        return
    signal.signal(signal.SIGTERM, _on_sigterm)


def report_destruct_errors(errors: tuple[DestructError, ...]) -> None:
    """Write one ``destruct_error:`` line per teardown failure to *stderr*.

    Never to stdout: a CLI's stdout is the command's output and may be piped into
    the next program, so a teardown failure must not contaminate it (T14-02).
    """
    for error in errors:
        print(f"destruct_error: {error.message}", file=sys.stderr)


class CliHost:
    """One container, one bridge loop, one invocation — the whole process."""

    def __init__(
        self,
        *,
        value_tokens: tuple[Any, ...],
        providers: Sequence[type] = (),
    ) -> None:
        """Build the container and register every provider it will ever hold.

        Registration happens before ``startup()`` because the graph is frozen
        when the container starts, and an override plan is only in effect while
        the host is being built (IC-11).
        """
        self.bridge = SyncBridge()
        self.container = build_container(value_tokens=value_tokens)
        for provider in providers:
            self.container.register(provider)
        self._started = False

    def resolve(self, unit: HostUnit, token: Any) -> Any:
        """Resolve ``token`` on the unit's scope, on the bridge's loop.

        Resolving on the container instead would hand back an instance that no
        scope owns and no ``finally`` destroys — the bug T14-10 rules out.
        """
        return self.bridge.run(unit.scope.resolve(token))

    @contextlib.contextmanager
    def invocation(self, *, values: Mapping[Any, Any]) -> Iterator[HostUnit]:
        """The close point: start once, open one unit, close it before exit.

        Yields:
            The open unit. Its scope is closed on the way out — including when
            the body raises, when Ctrl-C arrives, and when ``SIGTERM`` does.

        Raises:
            SystemExit: with :data:`EX_TEARDOWN` when teardown failed. A broken
                teardown contract must not look like a successful command.
        """
        if not self._started:
            self.bridge.run(self.container.startup())
            self._started = True
        unit = HostUnit(self.container, values=values, report=report_destruct_errors)
        self.bridge.run(unit.open())
        body_failed = True
        try:
            yield unit
            body_failed = False
        finally:
            try:
                scope_errors = self.bridge.run(unit.close())
                shutdown_errors = self.bridge.run(self.container.shutdown())
                report_destruct_errors(shutdown_errors)
            finally:
                # The loop is process-wide state too: close it after the last
                # container call, never before.
                self.bridge.close()
            if (scope_errors or shutdown_errors) and not body_failed:
                raise SystemExit(EX_TEARDOWN)
