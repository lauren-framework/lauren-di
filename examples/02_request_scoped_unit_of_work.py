"""Example 2 — a request-scoped unit of work with generator teardown.

Scenario
--------
Each request opens a database ``Session`` and wraps it in a ``UnitOfWork``.
Both are ``Scope.REQUEST``: inside one ``async with container.scope()`` block
every resolve returns the same instance, and when the block exits the
generator provider's code after ``yield`` runs exactly once.

What to look for
----------------
``events`` stays ``["open"]`` while the scope is live and only gains
``"close"`` after the block ends. A second scope gets its own session, which
is the whole point of request scope.

Run
---
    PYTHONPATH=. python examples/02_request_scoped_unit_of_work.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Annotated

from lauren_di import DIContainer, Inject, Scope, injectable

events: list[str] = []


class Session:
    """Stands in for a database connection."""

    def __init__(self, ident: int) -> None:
        self.ident = ident


@injectable(scope=Scope.REQUEST)
def session() -> Iterator[Session]:
    """A generator provider: before ``yield`` is setup, after is teardown."""
    events.append("open")
    yield Session(len(events))
    events.append("close")


@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    """A function provider's token is the function itself, so ``Inject`` names it.

    The annotation still carries the real type (``Session``), which is what a
    type checker sees; ``Inject(session)`` is only the runtime lookup key.
    """

    def __init__(self, db_session: Annotated[Session, Inject(session)]) -> None:
        self.session = db_session

    def work(self) -> int:
        return self.session.ident


async def main() -> None:
    container = DIContainer()
    container.register(session)
    container.register(UnitOfWork)

    async with container.scope() as scope:
        first = await scope.resolve(UnitOfWork)
        second = await scope.resolve(UnitOfWork)
        print("same within one request:", first is second)
        print("events while live:      ", events)

    print("events after teardown:  ", events)

    async with container.scope() as scope:
        third = await scope.resolve(UnitOfWork)

    print("next request, new session:", third.session is not first.session)


if __name__ == "__main__":
    asyncio.run(main())
