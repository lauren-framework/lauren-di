"""The cyclopts example: one default command, three providers, one close point.

cyclopts keeps its own loop: it *does* await ``async def`` commands and turns the
returned int into the process exit code. That is why every container call here
goes through this module's ``CliHost`` instead of the loop cyclopts is running.

    python examples/apps/cyclopts/cyclopts_app.py ada
    python examples/apps/cyclopts/cyclopts_app.py --mode fragile    # exit 70
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Annotated

import cyclopts

# ``cyclopts_di`` is imported first on purpose: it puts the checkout root on
# ``sys.path`` so ``lauren_di`` resolves when this file runs as a script.
# isort: off
# ``cyclopts_di`` must be imported first: the adapter puts ``examples/apps``
# and the checkout root on ``sys.path``, so neither ``_host`` nor
# ``lauren_di`` is importable before it.
from cyclopts_di import (
    EX_INTERRUPT,
    EX_TERMINATED,
    CliHost,
    Terminated,
    install_sigterm_handler,
)
from _host import HostUnit
from lauren_di import Inject, Scope, Token, injectable, post_construct, pre_destruct
# isort: on

#: The one value token: the per-invocation id the host supplies through ``values=``.
REQUEST_ID = Token("request_id")

#: Lifecycle trace the tests read. A demo has no other place to put it, and it is
#: what proves the close point ran before the process acted on the exit code.
EVENTS: list[str] = []

#: The modes the default command understands.
MODES = ("greet", "fragile", "stubborn", "boom", "interrupt")

#: What the ``async`` demo command returns, to prove cyclopts uses the value.
ASYNC_EXIT_CODE = 9


@injectable()
class Mailer:
    """APP-scoped: built by the first resolve, torn down by ``shutdown()``."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[str] = []

    @post_construct
    def open(self) -> None:
        """Record that startup reached this provider."""
        EVENTS.append("mailer.started")

    @pre_destruct
    def close(self) -> None:
        """Record the orderly teardown."""
        EVENTS.append("mailer.stopped")

    def send(self, text: str) -> str:
        """Keep ``text`` and return it, so callers can print what was sent."""
        self.sent.append(text)
        return text


@injectable()
class FragileMailer:
    """APP-scoped provider whose ``@pre_destruct`` raises: the T14-07 case."""

    def send(self, text: str) -> str:
        """Pretend to send ``text``."""
        return text

    @pre_destruct
    def close(self) -> None:
        """Fail during ``shutdown()`` — one ``DestructError``, exit 70."""
        EVENTS.append("fragile_mailer.stopped")
        raise RuntimeError("mailer refused to close")


@injectable(scope=Scope.REQUEST)
class Greeter:
    """REQUEST-scoped: one instance per invocation, built from mailer + token."""

    def __init__(self, mailer: Mailer, request_id: Annotated[str, Inject(REQUEST_ID)]) -> None:
        """Take the APP-scoped mailer and the invocation's id."""
        self.mailer = mailer
        self.request_id = request_id
        EVENTS.append("greeter.new")

    def greet(self, name: str) -> str:
        """Send ``name`` through the mailer and describe the invocation."""
        self.mailer.send(name)
        return f"hello {name} (request {self.request_id})"

    @pre_destruct
    def close(self) -> None:
        """Record the scope teardown."""
        EVENTS.append("greeter.closed")


@injectable(scope=Scope.REQUEST)
class FragileGreeter:
    """REQUEST-scoped provider whose ``@pre_destruct`` raises: the T14-02 case."""

    def __init__(self, mailer: Mailer, request_id: Annotated[str, Inject(REQUEST_ID)]) -> None:
        """Take the APP-scoped mailer and the invocation's id."""
        self.mailer = mailer
        self.request_id = request_id

    def greet(self, name: str) -> str:
        """Describe the invocation without pretending the teardown will work."""
        return f"hello {name} (request {self.request_id})"

    @pre_destruct
    def close(self) -> None:
        """Fail during scope teardown; the error is captured, not raised."""
        EVENTS.append("fragile_greeter.closed")
        raise RuntimeError("greeter refused to close")


# ``version_flags=[]`` keeps ``--version`` out of the way: this app is about the
# container, and a version flag would need a version to print.


def _greeter(host: CliHost, unit: HostUnit) -> Greeter:
    """Resolve the invocation's ``Greeter`` on this unit's scope.

    ``CliHost.resolve`` returns ``Any`` (the scope's API is token-typed), so the
    demo names the expected provider once, here, instead of at every call site.
    """
    greeter: Greeter = host.resolve(unit, Greeter)
    return greeter


app = cyclopts.App(name="cyclopts_app", version_flags=[], help_flags="--help")


@app.default
def main(
    name: str = "world",
    *,
    mode: str = "greet",
    hold: float = 0.0,
) -> int | None:
    """Run one command body and return the exit code cyclopts should use."""
    if mode not in MODES:
        raise cyclopts.CycloptsError(f"mode must be one of {', '.join(MODES)}")

    if mode == "fragile":
        host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileGreeter))
        with host.invocation(values={REQUEST_ID: name}) as unit:
            print(host.resolve(unit, FragileGreeter).greet(name))
        return None

    if mode == "stubborn":
        host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileMailer))
        with host.invocation(values={REQUEST_ID: name}) as unit:
            print(host.resolve(unit, FragileMailer).send(name))
        return None

    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: name}) as unit:
        print(_greeter(host, unit).greet(name))
        if mode == "boom":
            raise RuntimeError("the command body failed")
        if mode == "interrupt":
            raise KeyboardInterrupt
        if hold:
            print("holding", flush=True)
            time.sleep(hold)
    return None


def build_async_app() -> cyclopts.App:
    """Build an app whose command is ``async def`` and returns an exit code.

    cyclopts awaits the coroutine itself and exits with the returned int, so the
    process ends with :data:`ASYNC_EXIT_CODE` — the behaviour Typer lacks (T14-12).
    """
    async_app = cyclopts.App(name="cyclopts_async", version_flags=[])

    @async_app.default
    async def async_command() -> int:
        """Return the code cyclopts will exit with."""
        return ASYNC_EXIT_CODE

    return async_app


def build_annotated_app() -> cyclopts.App:
    """Build the app cyclopts *cannot* accept: a parameter that is a DI token.

    cyclopts tries to coerce the token into the annotated type, fails, prints its
    error panel and exits 2 — before any body, and therefore before any container
    (T14-09). The recipe resolves inside the body, as ``main`` above does.
    """
    broken = cyclopts.App(name="cyclopts_injected", version_flags=[])

    @broken.default
    def injected(mailer: Annotated[Mailer, Inject(Mailer)]) -> None:
        """Never runs: the parameter is bound as a CLI argument instead."""
        EVENTS.append("injected.ran")
        print(mailer)

    return broken


def run(argv: Sequence[str] | None = None) -> None:
    """Process entry point: SIGTERM and Ctrl-C still close the scope."""
    install_sigterm_handler()
    try:
        # cyclopts raises ``SystemExit`` with the command's return value, so the
        # close point inside the body has already run by the time it does.
        app(list(argv) if argv is not None else sys.argv[1:])
    except Terminated:
        print("scope closed after SIGTERM", file=sys.stderr)
        raise SystemExit(EX_TERMINATED) from None
    except KeyboardInterrupt:
        raise SystemExit(EX_INTERRUPT) from None


if __name__ == "__main__":
    run()
