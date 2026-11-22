# Recipes

Task-shaped answers to "how do I wire this?". Every recipe below is a whole
program: copy it, run it with `PYTHONPATH=. python recipe.py`, and it prints
something. For the concepts behind them see [Getting started](getting-started.md)
and the [guides](guides/custom-providers.md); for runnable variants with
commentary see [the examples](https://github.com/lauren-framework/lauren-di/blob/main/examples/README.md).

## Wiring configuration

### Read configuration from a plain dict

Use this when settings arrive as a `dict` at process start and you do not want a
settings class threaded through every constructor.

```python
from __future__ import annotations

import asyncio
from typing import Annotated

from lauren_di import DIContainer, Inject, Token, injectable

DB_URL = Token("db.url")
POOL_SIZE = Token("db.pool_size")


@injectable()
class Database:
    def __init__(
        self,
        url: Annotated[str, Inject(DB_URL)],
        pool_size: Annotated[int, Inject(POOL_SIZE)],
    ) -> None:
        self.url = url
        self.pool_size = pool_size

    def describe(self) -> str:
        return f"{self.url} (pool={self.pool_size})"


async def main() -> None:
    settings = {DB_URL: "postgres://localhost/app", POOL_SIZE: 5}

    container = DIContainer(value_tokens=[DB_URL, POOL_SIZE])
    container.register(Database)

    database = await container.resolve(Database, values=settings)
    print(database.describe())


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `Token` gives a non-class dependency a stable identity, and
`values=` supplies it per `resolve` call instead of binding it to the container.
`value_tokens` declares up front which tokens the host will hand in.

Gotcha: `values` is keyed by the token object, not by its name. A missing value
fails at resolve time with `MissingProviderError: No provider for Token(db.url)`
— the message names the token, not `Database`.

### Open a resource once

Use this when construction is trivial but a resource has to be opened before the
instance is used (a pool, a client handshake, a compiled grammar).

```python
from __future__ import annotations

import asyncio

from lauren_di import DIContainer, injectable, post_construct


@injectable()
class ConnectionPool:
    def __init__(self) -> None:
        self.connections: list[str] = []

    @post_construct
    async def open(self) -> None:
        self.connections.append("conn-1")


@injectable()
class Repository:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool


async def main() -> None:
    container = DIContainer()
    container.register(ConnectionPool)
    container.register(Repository)

    first = await container.resolve(Repository)
    second = await container.resolve(Repository)

    print(first.pool.connections, first.pool is second.pool)


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `@post_construct` is awaited by the container right after
`__init__`, before the instance is handed to anybody. Singleton scope means the
second `resolve` reuses the same pool — `['conn-1'] True`.

Gotcha: the hook runs when the singleton is *first built*, not at import or at
process start. To have everything open before your first request, call
`startup()` (next recipe).

### Start and stop the object graph

Use this when the process has a boot phase and a drain phase, and you want both
expressed as decorators on the provider instead of in a script.

```python
from __future__ import annotations

import asyncio

from lauren_di import DIContainer, injectable, post_construct, pre_destruct


@injectable()
class BackgroundWorker:
    def __init__(self) -> None:
        self.state = "new"

    @post_construct
    async def start(self) -> None:
        self.state = "running"

    @pre_destruct
    async def stop(self) -> None:
        self.state = "stopped"


async def main() -> None:
    container = DIContainer()
    container.register(BackgroundWorker)

    await container.startup()
    worker = await container.resolve(BackgroundWorker)
    print("after startup:", worker.state)

    await container.shutdown()
    print("after shutdown:", worker.state)


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `startup()` compiles the graph and constructs every singleton, so
`@post_construct` has already run when the first `resolve` returns.
`shutdown()` walks the same set backwards and awaits `@pre_destruct`.

Gotcha: `lauren_di` has no scheduler of its own — nothing calls `shutdown()` for
you. Call it from the `finally` of your entrypoint, or you lose the drain.

## Shaping a request

### Give a request its own unit of work

Use this when a session or transaction must live exactly as long as one request
and be closed even when the handler raises.

```python
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from lauren_di import DIContainer, Scope, injectable, use_factory


@injectable(scope=Scope.REQUEST)
class Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def close(self) -> None:
        self.statements.append("closed")


@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, sql: str) -> None:
        self.session.statements.append(sql)


async def open_unit_of_work(session: Session) -> AsyncIterator[UnitOfWork]:
    unit_of_work = UnitOfWork(session)
    try:
        yield unit_of_work
    finally:
        await session.close()


@injectable(scope=Scope.REQUEST)
class CreateOrderHandler:
    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self.unit_of_work = unit_of_work

    async def handle(self, order_id: str) -> None:
        await self.unit_of_work.execute(f"insert {order_id}")


async def main() -> None:
    container = DIContainer()
    container.register(Session)
    container.register_custom(
        use_factory(
            provide=UnitOfWork,
            factory=open_unit_of_work,
            injects=[Session],
            scope=Scope.REQUEST,
        )
    )
    container.register(CreateOrderHandler)

    async with container.scope() as scope:
        handler = await scope.resolve(CreateOrderHandler)
        await handler.handle("order-1")
        session = handler.unit_of_work.session

    print(session.statements)


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: a generator factory is entered on first resolve inside the scope
and resumed at `async with` exit, so `finally` runs once per request — the output
is `['insert order-1', 'closed']`. `injects=[Session]` tells the factory which
argument to hand it; without it the call fails with a plain `TypeError`.

Gotcha: rollback does not belong in the generator's `except`. An exception raised
in the request body does not reach the generator — the scope closes it with
`GeneratorExit`, so `finally` fires but `except Exception` never does. Commit or
roll back in the handler.

### Share one object across collaborators in a request

Use this when several collaborators in the same request must agree on one value
(a request id, a tenant, a correlation token) and must not leak it into the next
request.

```python
from __future__ import annotations

import asyncio
import itertools

from lauren_di import DIContainer, Scope, injectable

COUNTER = itertools.count(1)


@injectable(scope=Scope.REQUEST)
class RequestContext:
    def __init__(self) -> None:
        self.request_id = f"req-{next(COUNTER)}"


@injectable(scope=Scope.REQUEST)
class AuditLog:
    def __init__(self, context: RequestContext) -> None:
        self.context = context

    def line(self) -> str:
        return f"audit {self.context.request_id}"


@injectable(scope=Scope.REQUEST)
class Router:
    def __init__(self, context: RequestContext) -> None:
        self.context = context


async def main() -> None:
    container = DIContainer()
    container.register(RequestContext)
    container.register(AuditLog)
    container.register(Router)

    async with container.scope() as first:
        audit = await first.resolve(AuditLog)
        router = await first.resolve(Router)
        print("same id:", audit.context is router.context, audit.line())

    async with container.scope() as second:
        later = await second.resolve(AuditLog)
        print("fresh id:", later.context is not audit.context, later.line())


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: request scope is a cache per `container.scope()` block, so
`RequestContext` is built once and reused by `AuditLog` and `Router` — then
thrown away when the block ends and rebuilt in the next one.

Gotcha: every consumer in the chain must be `Scope.REQUEST`. Mark one of them
`@injectable()` and it becomes a singleton that captures the first request's
context forever — the id stops changing and the leak is silent.

## Varying per build

### Make a dependency optional

Use this when a collaborator exists in some deployments and not others — metrics,
tracing, an audit sink — and you do not want two provider classes.

```python
from __future__ import annotations

import asyncio

from lauren_di import DIContainer, OptionalDep, Token, use_factory, use_value

METRICS = Token("metrics.sink")


class MetricsSink:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def record(self, key: str) -> None:
        self.keys.append(key)


class Cache:
    def __init__(self, sink: MetricsSink | None) -> None:
        self.sink = sink

    def get(self, key: str) -> str:
        if self.sink is not None:
            self.sink.record(key)
        return f"value:{key}"


def build(sink: MetricsSink | None = None) -> DIContainer:
    container = DIContainer()
    if sink is not None:
        container.register_custom(use_value(provide=METRICS, value=sink))
    container.register_custom(
        use_factory(provide=Cache, factory=Cache, injects=[OptionalDep(METRICS)])
    )
    return container


async def main() -> None:
    plain = await build().resolve(Cache)
    print("no sink:", plain.sink, plain.get("a"))

    sink = MetricsSink()
    measured = await build(sink).resolve(Cache)
    measured.get("b")
    print("sink:", measured.sink is sink, sink.keys)


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `OptionalDep(token)` resolves to `None` when the token has no
provider instead of raising `MissingProviderError`, so the same `Cache` serves
both builds.

Gotcha: `OptionalDep` is a *provider argument*, not an annotation — it only
appears inside `injects=[...]`. There is no `Cache(sink: Sink | None = None)`
shortcut; the `| None` default does not make the dependency optional.

### Swap an implementation in tests

Use this when a test needs one collaborator replaced and everything else left
exactly as production builds it.

```python
from __future__ import annotations

import asyncio
from datetime import datetime

from lauren_di import DIContainer, injectable, use_value


@injectable()
class Clock:
    def now(self) -> datetime:
        return datetime.now()


class FrozenClock(Clock):
    def now(self) -> datetime:
        return datetime(2024, 1, 1, 12, 0)


@injectable()
class ReportService:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def stamp(self) -> str:
        return self.clock.now().isoformat()


def build(clock: Clock | None = None) -> DIContainer:
    container = DIContainer()
    if clock is None:
        container.register(Clock)
    else:
        container.register_custom(use_value(provide=Clock, value=clock))
    container.register(ReportService)
    return container


async def main() -> None:
    production = await build().resolve(ReportService)
    under_test = await build(FrozenClock()).resolve(ReportService)
    print("production:", production.clock.__class__.__name__)
    print("test:", under_test.stamp())


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: the branch lives in one `build()` function, so the test path and
the production path register the same graph and differ in a single row.
`use_value` binds an instance you already own.

Gotcha: `use_value` hands out the *same object* every time — do not pass a
mutable fake that a test expects to be fresh per resolve. Use a factory for that.

### Rehearse a build with an override plan

Use this when the swap has to apply to a build you do not own — a feature-flag
rehearsal, a staging smoke test — and editing `build()` is not an option.

```python
from __future__ import annotations

import asyncio

from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable


@injectable()
class Clock:
    def now(self) -> str:
        return "wall"


class FrozenClock:
    def now(self) -> str:
        return "frozen"


@injectable()
class ReportService:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock


def build(plan: OverridePlan | None = None) -> DIContainer:
    container = DIContainer(overrides=plan)
    container.register(Clock)
    container.register(ReportService)
    return container


async def main() -> None:
    production = await build().resolve(ReportService)

    plan = OverridePlan(
        providers={
            Clock: ProviderOverride(token=Clock, kind="class", use_class=FrozenClock)
        }
    )
    rehearsal = await build(plan).resolve(ReportService)

    print("production:", production.clock.now(), "rehearsal:", rehearsal.clock.now())


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: an `OverridePlan` is passed to the container constructor and
applies to that one build; the untouched build keeps the real `Clock`.

Gotcha: `OverridePlan` takes a mapping keyed by token, not a list. And the
payload must match `kind`: `kind="class"` needs `use_class=...` — passing
`value=` instead raises `OverrideError: ... bad_payload` at build time.

## Structuring a codebase

### Bind a protocol to an implementation

Use this when the consumer should depend on an interface and the choice of
implementation is a deployment decision.

```python
from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from lauren_di import DIContainer, injectable, use_class


@runtime_checkable
class Notifier(Protocol):
    def send(self, text: str) -> str: ...


class EmailNotifier:
    def send(self, text: str) -> str:
        return f"email:{text}"


class SmsNotifier:
    def send(self, text: str) -> str:
        return f"sms:{text}"


@injectable()
class Signup:
    def __init__(self, notifier: Notifier) -> None:
        self.notifier = notifier

    def welcome(self, user: str) -> str:
        return self.notifier.send(f"welcome {user}")


async def main() -> None:
    container = DIContainer()
    container.register_custom(use_class(provide=Notifier, use=EmailNotifier))
    container.register(Signup)

    signup = await container.resolve(Signup)
    print(signup.welcome("ada"))


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `use_class` creates a row whose token is the protocol, so
`Signup(notifier: Notifier)` is satisfied by `EmailNotifier` without `Signup`
knowing the class name. Swap `use=EmailNotifier` for `use=SmsNotifier` and
nothing else moves.

Gotcha: the implementation is not registered under its own type by this row —
only the protocol token exists. Register `EmailNotifier` separately if some other
consumer asks for it directly.

### Build a plugin registry

Use this when a set of independent providers should all be injected into one
consumer, in registration order, without the consumer importing them.

```python
from __future__ import annotations

import asyncio
from typing import Protocol

from lauren_di import DIContainer, injectable


class Plugin(Protocol):
    name: str

    def run(self) -> str: ...


@injectable(multi=True, provides=[Plugin])
class AlphaPlugin:
    name = "alpha"

    def run(self) -> str:
        return "alpha ran"


@injectable(multi=True, provides=[Plugin])
class BetaPlugin:
    name = "beta"

    def run(self) -> str:
        return "beta ran"


@injectable()
class Registry:
    def __init__(self, plugins: list[Plugin]) -> None:
        self.plugins = plugins

    def run_all(self) -> list[str]:
        return [plugin.run() for plugin in self.plugins]


async def main() -> None:
    container = DIContainer()
    container.register(AlphaPlugin)
    container.register(BetaPlugin)
    container.register(Registry)

    registry = await container.resolve(Registry)
    print([plugin.name for plugin in registry.plugins])
    print(registry.run_all())


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `multi=True` allows many rows per token and `list[Plugin]` in the
consumer asks for all of them — `['alpha', 'beta']`, then
`['alpha ran', 'beta ran']`. Adding a plugin is one new class plus one
`register`.

Gotcha: `multi=True` alone is not enough. Without `provides=[Plugin]` the rows
live under their own class tokens and the consumer fails with
`MissingProviderError: No provider for Plugin (requested as list)`.

### Keep a module's internals private

Use this when a codebase has domains that should not reach into each other's
providers except through declared grants.

```python
from __future__ import annotations

import asyncio

from lauren_di import DIContainer, injectable


class BillingModule:
    pass


class ApiModule:
    pass


@injectable()
class Ledger:
    def total(self) -> int:
        return 42


@injectable()
class Invoice:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger


async def main() -> None:
    container = DIContainer()
    container.register(Ledger, owning_module=BillingModule)
    container.register(Invoice, owning_module=ApiModule)
    container.set_visible(ApiModule, frozenset({Ledger}))

    invoice = await container.resolve(Invoice, owning_module=ApiModule)
    print(invoice.ledger.total())

    try:
        await container.resolve(Invoice)
    except Exception as error:
        print(type(error).__name__, "-", error)


if __name__ == "__main__":
    asyncio.run(main())
```

Why it works: `owning_module` records which domain a row belongs to and
`set_visible` declares what a domain may see, so `ApiModule` reaches `Ledger`
only because it was granted it. Visibility is opt-in: until the first
`set_visible` call the container is a flat registry.

Gotcha: once rows carry an owner and grants exist, a bare `container.resolve`
speaks for the global module and can fail with
`MissingProviderError: No provider for Invoice visible from module <global>`.
Resolve through the module that owns the graph, or grant the row to it.

## Where to go next

- [Scopes](guides/scopes.md) — what `Scope.SINGLETON` and `Scope.REQUEST` mean
  for caching and teardown.
- [Custom providers](guides/custom-providers.md) — the full provider vocabulary
  behind `use_class`, `use_value` and `use_factory`.
- [Errors](errors.md) — every error in this page, with the fix.
- [Examples](https://github.com/lauren-framework/lauren-di/blob/main/examples/README.md) — the same recipes with longer commentary.
