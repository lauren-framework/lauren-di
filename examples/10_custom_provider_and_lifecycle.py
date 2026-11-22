"""Example 10 — custom providers, generator teardown, startup and shutdown.

Scenario
--------
Two shapes that are not ``@injectable`` classes:

* ``use_factory`` — a ``CustomProvider`` record that computes a value from
other dependencies. Here a connection-pool description is built from
``Settings``. The factory needs no decorator; ``injects=`` names the
dependencies it receives positionally.
* a **generator provider** — ``@injectable()`` on a generator function, where
the code before ``yield`` is setup and the code after runs at teardown. A
generator is a *provider shape*, so it is spelled with the decorator: a plain
``use_factory`` factory returns its value directly and is not driven as a
generator.

``startup()`` builds every singleton, so the factory and the generator's setup
both run before the first ``resolve``. ``shutdown()`` runs teardown and
returns failures rather than raising them.

Run
---
    PYTHONPATH=. python examples/10_custom_provider_and_lifecycle.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

from lauren_di import DIContainer, injectable, use_factory

log: list[str] = []


@injectable()
class Settings:
    """Input to the factory below."""

    def __init__(self) -> None:
        self.dsn = "postgresql://localhost/app"


def build_pool(settings: Settings) -> str:
    """A plain factory: returns its value, so no decorator is needed."""
    log.append("pool-built")
    return f"pool({settings.dsn})"


@injectable()
def cache() -> Iterator[str]:
    """A generator provider: setup before ``yield``, teardown after it."""
    log.append("cache-open")
    try:
        yield "cache"
    finally:
        log.append("cache-close")


async def main() -> None:
    container = DIContainer()
    container.register(Settings)
    container.register(cache)
    container.register_custom(
        use_factory(provide="POOL", factory=build_pool, injects=[Settings]),
    )

    await container.startup()
    print("log after startup:", log)

    print("pool:            ", await container.resolve("POOL"))
    print("cache:           ", await container.resolve(cache))

    failures = await container.shutdown()
    print("log after shutdown:", log)
    print("teardown failures:", failures)


if __name__ == "__main__":
    asyncio.run(main())
