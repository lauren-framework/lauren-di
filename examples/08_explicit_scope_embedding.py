"""Example 8 — drive a request scope by hand, with no framework.

Scenario
--------
``lauren_di`` has no HTTP layer, so a request scope is just an
``async with container.scope(...)`` block you write yourself: a CLI command, a
job handler, a test. Everything resolved inside shares one request cache; the
block's exit runs teardown and leaves ``scope.closed`` true.

``values=`` carries per-request data — here a tenant id — into the scope, so a
request-scoped service can read it without a global.

What to look for
----------------
The singleton ``AppConfig`` is the *same* object in both scopes, while the
request-scoped ``RequestId`` is fresh in each. That difference is the whole
scope model, and no framework is involved.

Run
---
    PYTHONPATH=. python examples/08_explicit_scope_embedding.py
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from lauren_di import DIContainer, Inject, Scope, Token, injectable

TENANT = Token("tenant")


@injectable()
class AppConfig:
    """Singleton: shared by every request in the process."""

    def __init__(self) -> None:
        self.dsn = "sqlite:///app.db"


@injectable(scope=Scope.REQUEST)
class RequestId:
    """Request scope: a fresh identifier for each scope."""

    def __init__(self) -> None:
        self.value = uuid.uuid4().hex[:8]


@injectable(scope=Scope.REQUEST)
class TenantContext:
    """Reads the tenant that ``container.scope(values=...)`` supplied."""

    def __init__(self, tenant: Annotated[str, Inject(TENANT)]) -> None:
        self.tenant = tenant


async def main() -> None:
    container = DIContainer(value_tokens=[TENANT])
    container.register(AppConfig)
    container.register(RequestId)
    container.register(TenantContext)

    async with container.scope(values={TENANT: "acme"}) as scope:
        config_a = await scope.resolve(AppConfig)
        request_a = await scope.resolve(RequestId)
        again = await scope.resolve(RequestId)
        tenant_a = await scope.resolve(TenantContext)
        print("within one scope, same request id:", request_a is again)
        print("tenant:", tenant_a.tenant)

    async with container.scope(values={TENANT: "globex"}) as scope:
        config_b = await scope.resolve(AppConfig)
        request_b = await scope.resolve(RequestId)
        tenant_b = await scope.resolve(TenantContext)
        print("tenant:", tenant_b.tenant)

    print("singleton shared across scopes:", config_a is config_b)
    print("request id fresh per scope:   ", request_a is not request_b)
    print("scope closed after the block: ", scope.closed)


if __name__ == "__main__":
    asyncio.run(main())
