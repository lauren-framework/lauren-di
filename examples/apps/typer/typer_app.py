"""The Typer example: one command, three providers, one close point.

Typer collapses a single-command app, so ``typer_app.py`` takes a NAME argument
and a ``--mode`` option instead of sub-commands — that shape is what makes
``invoke(app, [])`` run the body (T14-15).

    python examples/apps/typer/typer_app.py ada
    python examples/apps/typer/typer_app.py --mode fragile    # exit 70
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Annotated

import typer

# isort: off
# ``typer_di`` must be imported first: the adapter puts ``examples/apps``
# and the checkout root on ``sys.path``, so neither ``_host`` nor
# ``lauren_di`` is importable before it.
from typer_di import (
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

#: The modes the one command understands.
MODES = ("greet", "fragile", "stubborn", "boom", "interrupt")


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


app = typer.Typer(add_completion=False, help="Host lauren_di inside Typer.")


@app.command()
def main(
    name: Annotated[str, typer.Argument(help="who to greet")] = "world",
    mode: Annotated[str, typer.Option("--mode", help="greet|fragile|stubborn|boom|interrupt")] = (
        "greet"
    ),
    hold: Annotated[
        float, typer.Option("--hold", help="sleep this many seconds in the body")
    ] = 0.0,
) -> None:
    """Run one command body. Declared ``def``: Typer never awaits ``async def``."""
    if mode not in MODES:
        raise typer.BadParameter(f"mode must be one of {', '.join(MODES)}")

    if mode == "fragile":
        host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileGreeter))
        with host.invocation(values={REQUEST_ID: name}) as unit:
            typer.echo(host.resolve(unit, FragileGreeter).greet(name))
        return

    if mode == "stubborn":
        host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileMailer))
        with host.invocation(values={REQUEST_ID: name}) as unit:
            typer.echo(host.resolve(unit, FragileMailer).send(name))
        return

    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: name}) as unit:
        typer.echo(_greeter(host, unit).greet(name))
        if mode == "boom":
            raise RuntimeError("the command body failed")
        if mode == "interrupt":
            raise KeyboardInterrupt
        if hold:
            typer.echo("holding")
            time.sleep(hold)


def build_annotated_app() -> typer.Typer:
    """Build the app Typer *cannot* accept: a parameter that is a DI token.

    Typer turns every parameter into a CLI parameter, and the injection marker is
    not a type it can convert, so the command never becomes runnable (T14-08).
    The recipe therefore resolves inside the body, as ``main`` above does.
    """
    broken = typer.Typer(add_completion=False)

    @broken.command()
    def injected(mailer: Annotated[Mailer, Inject(Mailer)]) -> None:
        """Never runs: the parameter is bound as a CLI argument instead."""
        EVENTS.append("injected.ran")
        typer.echo(str(mailer))

    return broken


def build_async_app() -> typer.Typer:
    """Build the app Typer silently drops: a command declared ``async def``.

    Typer calls the function and never awaits the coroutine, so the body does not
    run and the process still exits 0 — the reason every command here is ``def``
    (T14-04).
    """
    async_app = typer.Typer(add_completion=False)

    @async_app.command()
    async def async_command() -> None:
        """Never runs: Typer drops the coroutine (T14-04)."""
        EVENTS.append("async.ran")

    return async_app


def run(argv: Sequence[str] | None = None) -> None:
    """Process entry point: SIGTERM and Ctrl-C still close the scope."""
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
