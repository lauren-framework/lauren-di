"""The argparse example: the same three providers, five commands, one close point.

Run it as a script (one process per command, so one container per process):

    python examples/apps/argparse/argparse_app.py greet ada
    python examples/apps/argparse/argparse_app.py fragile    # exit 70
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from typing import Annotated

# isort: off
# ``argparse_di`` must be imported first: the adapter puts ``examples/apps``
# and the checkout root on ``sys.path``, so neither ``_host`` nor
# ``lauren_di`` is importable before it.
from argparse_di import (
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


def _greet(args: argparse.Namespace) -> int:
    """Greet ``args.name`` from a REQUEST-scoped provider."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: args.name}) as unit:
        print(_greeter(host, unit).greet(args.name))
    return 0


def _fragile(args: argparse.Namespace) -> int:
    """Resolve a provider whose scope teardown fails: exit 70, one stderr line."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileGreeter))
    with host.invocation(values={REQUEST_ID: "fragile"}) as unit:
        print(host.resolve(unit, FragileGreeter).greet("fragile"))
    return 0


def _stubborn(args: argparse.Namespace) -> int:
    """Resolve a provider whose ``shutdown()`` teardown fails: exit 70."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, FragileMailer))
    with host.invocation(values={REQUEST_ID: "stubborn"}) as unit:
        print(host.resolve(unit, FragileMailer).send("stubborn"))
    return 0


def _boom(args: argparse.Namespace) -> int:
    """Raise from the body: the scope still closes, the traceback reaches the user."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "boom"}) as unit:
        _greeter(host, unit)
        raise RuntimeError("the command body failed")


def _interrupt(args: argparse.Namespace) -> int:
    """Take a Ctrl-C in the body and let the ``finally`` close the scope."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "interrupt"}) as unit:
        _greeter(host, unit)
        raise KeyboardInterrupt


def _hold(args: argparse.Namespace) -> int:
    """Sleep so a test can deliver SIGTERM while the command body runs."""
    host = CliHost(value_tokens=(REQUEST_ID,), providers=(Mailer, Greeter))
    with host.invocation(values={REQUEST_ID: "hold"}) as unit:
        _greeter(host, unit)
        print("holding", flush=True)
        time.sleep(args.seconds)
    return 0


def build_parser(prog: str = "argparse_app") -> argparse.ArgumentParser:
    """Build ``prog <command> [args]``: one sub-parser per command.

    Every sub-parser is ``required=True``, so ``main(["prog"])`` is a usage error
    that exits 2 before any command body — and therefore before any container —
    runs (T14-01).
    """
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    greet = commands.add_parser("greet", help="greet NAME from a REQUEST-scoped provider")
    greet.add_argument("name")
    greet.set_defaults(handler=_greet)

    commands.add_parser("fragile", help="fail the scope teardown: exit 70").set_defaults(
        handler=_fragile
    )
    commands.add_parser("stubborn", help="fail the shutdown teardown: exit 70").set_defaults(
        handler=_stubborn
    )
    commands.add_parser("boom", help="raise from the command body").set_defaults(handler=_boom)
    commands.add_parser("interrupt", help="raise KeyboardInterrupt in the body").set_defaults(
        handler=_interrupt
    )

    hold = commands.add_parser("hold", help="sleep so SIGTERM can land mid-body")
    hold.add_argument("seconds", type=float, nargs="?", default=5.0)
    hold.set_defaults(handler=_hold)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return the process exit code.

    Args:
        argv: The full argv, program name included; ``sys.argv`` when omitted.
            ``main(["prog"])`` therefore means "prog with no command" (T14-01).

    Returns:
        The exit code: 0 on success, 2 for a usage error, 70 for a broken
        teardown contract, 130 for Ctrl-C and 143 for SIGTERM.
    """
    words = list(sys.argv if argv is None else argv)
    try:
        args = build_parser(words[0]).parse_args(words[1:])
    except SystemExit as stop:
        # ``--help`` exits 0, a usage error 2. Either way nothing has started, so
        # the code is returned rather than raised: no container to unwind.
        return int(stop.code or 0)

    install_sigterm_handler()
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except Terminated:
        print("scope closed after SIGTERM", file=sys.stderr)
        return EX_TERMINATED
    except KeyboardInterrupt:
        return EX_INTERRUPT
    except SystemExit as stop:
        # The close point decided the code (a teardown failure is 70).
        return int(stop.code or 0)


if __name__ == "__main__":
    sys.exit(main())
