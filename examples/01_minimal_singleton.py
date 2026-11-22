"""Example 1 — the smallest useful container: two singletons and one edge.

Scenario
--------
``Config`` holds the database DSN and ``Greeter`` needs it. Both are
registered as singletons, so the container constructs each exactly once and
injects the same ``Config`` into ``Greeter``.

What to look for
----------------
``resolve`` returns the *same* ``Greeter`` twice: singleton scope caches the
instance on the container, so the second call does no work and the nested
``Config`` is shared too.

Run
---
    PYTHONPATH=. python examples/01_minimal_singleton.py
"""

from __future__ import annotations

import asyncio

from lauren_di import DIContainer, injectable


@injectable()
class Config:
    """Application configuration: one instance for the process."""

    def __init__(self) -> None:
        self.dsn = "sqlite:///app.db"


@injectable()
class Greeter:
    """Depends on ``Config``; the container fills it from the annotation."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def greet(self) -> str:
        return f"hello from {self.config.dsn}"


async def main() -> None:
    container = DIContainer()
    container.register(Config)
    container.register(Greeter)
    await container.startup()

    first = await container.resolve(Greeter)
    second = await container.resolve(Greeter)

    print(first.greet())
    print("same greeter: ", first is second)
    print("same config:  ", first.config is second.config)


if __name__ == "__main__":
    asyncio.run(main())
