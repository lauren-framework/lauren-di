"""The click example: three providers, five commands, one close point.

Run it as a script (one process per command, so one container per process):

    python examples/apps/click/click_app.py greet ada
    python examples/apps/click/click_app.py fragile    # exit 70
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Annotated

import click

# isort: off
# ``click_di`` must be imported first: the adapter puts ``examples/apps``
# and the checkout root on ``sys.path``, so neither ``_host`` nor
# ``lauren_di`` is importable before it.
from click_di import (
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


def _greeter(host: CliHost, unit: HostUnit) -> Greeter:
    """Resolve the invocation's ``Greeter`` on this unit's scope.

    ``CliHost.resolve`` returns ``Any`` (the scope's API is token-typed), so the
    demo names the expected provider once, here, instead of at every call site.
    """
    greeter: Greeter = host.resolve(unit, Greeter)
    return greeter


@click.group()
def app() -> None:
    """Host ``lauren_di`` inside click."""


@app.command()
@click.argument("name")
def greet(name: str) -> None:
    """Greet NAME from a REQUEST-scoped provider.

    Click has already bound ``name``, so this is the earliest point at which the
    container may start: a usage error above never builds a graph (T14-01).
    """
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: name}) as unit:
        click.echo(_greeter(host, unit).greet(name))


@app.command()
def fragile() -> None:
    """Resolve a provider whose scope teardown fails: exit 70, one stderr line."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileGreeter))
    with host.invocation(values={REQUEST_ID: "fragile"}) as unit:
        click.echo(host.resolve(unit, FragileGreeter).greet("fragile"))


@app.command()
def stubborn() -> None:
    """Resolve a provider whose ``shutdown()`` teardown fails: exit 70."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileMailer))
    with host.invocation(values={REQUEST_ID: "stubborn"}) as unit:
        click.echo(host.resolve(unit, FragileMailer).send("stubborn"))


@app.command()
def boom() -> None:
    """Raise from the body: the exception reaches click, the scope still closes."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "boom"}) as unit:
        _greeter(host, unit)
        raise RuntimeError("the command body failed")


@app.command()
def interrupt() -> None:
    """Take a Ctrl-C in the body and let the ``finally`` close the scope."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "interrupt"}) as unit:
        _greeter(host, unit)
        raise KeyboardInterrupt


@app.command()
@click.argument("seconds", type=float, default=5.0)
def hold(seconds: float) -> None:
    """Sleep so a test can deliver SIGTERM while the command body runs."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "hold"}) as unit:
        _greeter(host, unit)
        click.echo("holding")
        time.sleep(seconds)


def run(argv: Sequence[str] | None = None) -> None:
    """Process entry point: SIGTERM and Ctrl-C still close the scope.

    Anything printed here is printed *after* the command's ``with`` block
    unwound, which is what makes the marker in the SIGTERM test evidence.
    """
    install_sigterm_handler()
    try:
        app(args=list(argv) if argv is not None else None, standalone_mode=True)
    except Terminated:
        print("scope closed after SIGTERM", file=sys.stderr)
        raise SystemExit(EX_TERMINATED) from None
    except KeyboardInterrupt:
        raise SystemExit(EX_INTERRUPT) from None


if __name__ == "__main__":
    run(sys.argv[1:])
