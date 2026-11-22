"""Example 7 — ``@post_construct`` / ``@pre_destruct`` ordering.

Scenario
--------
A ``Connection`` in a small class hierarchy. Construction hooks run **base
class first**, then the subclass, in definition order within each class.
Destruction hooks run **subclass first**, then the base — teardown reverses
construction.

What to look for
----------------
``startup()`` fires every singleton's ``@post_construct`` before the first
resolve, so the recorded order starts ``base_setup, child_setup``. ``shutdown()``
runs ``@pre_destruct`` newest-first and *returns* failures instead of raising
them, so one bad hook cannot strand the rest; here the tuple is empty.

Run
---
    PYTHONPATH=. python examples/07_lifecycle_ordering.py
"""

from __future__ import annotations

import asyncio

from lauren_di import DIContainer, injectable, post_construct, pre_destruct

order: list[str] = []


@injectable()
class Connection:
    """The base class owns the hooks every connection shares."""

    @post_construct
    def open(self) -> None:
        order.append("base_setup")

    @pre_destruct
    def close(self) -> None:
        order.append("base_teardown")


@injectable()
class PooledConnection(Connection):
    """The subclass adds its own hooks; both classes' hooks run."""

    @post_construct
    def warm(self) -> None:
        order.append("child_setup")

    @pre_destruct
    def drain(self) -> None:
        order.append("child_teardown")


async def main() -> None:
    container = DIContainer()
    container.register(PooledConnection)

    await container.startup()
    print("after startup: ", order)

    failures = await container.shutdown()
    print("after shutdown:", order)
    print("teardown failures:", failures)


if __name__ == "__main__":
    asyncio.run(main())
