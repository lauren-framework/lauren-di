"""Example 3 — configuration and credentials through ``Token``-keyed values.

Scenario
--------
A ``Mailer`` needs two strings that are not classes: a DSN and an API key.
They are identified by ``Token`` objects, declared as *value tokens* on the
container, and supplied per build through ``values=``. Nothing is baked into
the container, so the same ``Mailer`` class serves dev, test and production.

Why ``value_tokens=``
---------------------
A bare ``str`` annotation cannot be a DI token — every string would collide —
and a ``Token`` cannot appear in an annotation as a type, so the annotation
keeps the real type and ``Inject`` names the token. ``value_tokens=`` tells
the compiler those tokens are satisfied by ``values=`` rather than by a
provider, so ``compile()`` does not report them as missing.

Run
---
    PYTHONPATH=. python examples/03_config_and_credentials.py
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from lauren_di import DIContainer, Inject, Token, injectable

DSN = Token("dsn")
API_KEY = Token("api_key")


@injectable()
class Mailer:
    """Both dependencies are strings identified by tokens, not by type."""

    def __init__(
        self,
        dsn: Annotated[str, Inject(DSN)],
        api_key: Annotated[str, Inject(API_KEY)],
    ) -> None:
        self.dsn = dsn
        self.api_key = api_key

    def describe(self) -> str:
        masked = self.api_key[:2] + "*" * max(len(self.api_key) - 2, 0)
        return f"mailer dsn={self.dsn} key={masked}"


async def build(values: dict[object, str]) -> Mailer:
    """One build: declare the value tokens, register the consumer, resolve."""
    container = DIContainer(value_tokens=[DSN, API_KEY])
    container.register(Mailer)
    return await container.resolve(Mailer, values=values)


async def main() -> None:
    production = await build(
        {DSN: "smtp://mail.example.com:587", API_KEY: "live-abc123"},
    )
    test = await build({DSN: "memory://", API_KEY: "test-zzz999"})

    print("production:", production.describe())
    print("test:      ", test.describe())
    print("independent builds:", production is not test)


if __name__ == "__main__":
    asyncio.run(main())
