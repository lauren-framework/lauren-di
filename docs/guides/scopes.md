# Scopes and lifecycle

## The three scopes

`Scope` is an ordered `IntEnum`. The numeric order is load-bearing: the
compiler rejects an edge whose consumer is **wider** than its dependency.

| Scope | Value | Cached in | Meaning |
|---|---|---|---|
| `Scope.TRANSIENT` | 0 | nowhere | fresh instance per resolve |
| `Scope.REQUEST` | 1 | the enclosing `ResolutionScope` | one per scope |
| `Scope.SINGLETON` | 2 | the container | one per container |

```python
from lauren_di import Scope

assert Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON
assert Scope.REQUEST.label == "request"
assert str(Scope.REQUEST) == "request"   # stable on every interpreter
```

`label` is the spelling every message and log line uses. `str()` is routed
through it because `IntEnum.__str__` changed between Python versions.

## The narrowing rule

The six consumer/dependency pairs, evaluated by `compile()`:

| consumer ↓ dependency → | `TRANSIENT` | `REQUEST` | `SINGLETON` |
|---|---|---|---|
| `TRANSIENT` | ok | ok | ok |
| `REQUEST` | ok | ok | ok |
| `SINGLETON` | ok | **error** | ok |

The rule in one sentence: *a consumer may not outlive the instance it
receives.* A singleton outlives a request, so it may not depend on one.

```python
from lauren_di import DIScopeViolationError, DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


@injectable(scope=Scope.SINGLETON)
class Wide:
    session: Session


container = DIContainer()
container.register(Session)
container.register(Wide)
try:
    container.compile()
except DIScopeViolationError as exc:
    assert exc.detail["dependent"] == "Wide"
    assert exc.detail["dependent_scope"] == "singleton"
    assert exc.detail["dependency"] == "Session"
    assert exc.detail["dependency_scope"] == "request"
```

## Owning a request cache

`Scope.REQUEST` instances live in a `ResolutionScope`. Create one with
`async with container.scope() as scope:`; everything resolved through it shares
one cache, and exiting the block tears those instances down.

```python
import asyncio

from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


async def main() -> None:
    container = DIContainer()
    container.register(Session)

    async with container.scope() as scope:
        first = await scope.resolve(Session)
        second = await scope.resolve(Session)
        assert first is second          # one cache per scope

    async with container.scope() as other:
        assert await other.resolve(Session) is not first


asyncio.run(main())
```

`container.resolve(T)` with no scope is the unscoped path. Its behaviour depends
on `strict_request_scope`:

| `strict_request_scope` | resolving a `REQUEST` provider outside a scope |
|---|---|
| `False` (default) | logs a one-time warning, returns an **uncached** instance |
| `True` | raises `DIScopeViolationError` with `reason="no_active_scope"` |

```python
from lauren_di import DIScopeViolationError, DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


strict = DIContainer(strict_request_scope=True)
strict.register(Session)

import asyncio


def check() -> None:
    async def main() -> None:
        try:
            await strict.resolve(Session)
        except DIScopeViolationError as exc:
            assert exc.detail["reason"] == "no_active_scope"

    asyncio.run(main())


check()
```

Default (lax) mode still resolves, but the instance is never cached and never
reaches a teardown — which is why strict mode exists.

## Lifecycle hooks

`@post_construct` runs after an instance is built; `@pre_destruct` runs before
it is discarded. Both are collected across the MRO: **bases first on startup,
subclasses first on teardown**.

Hooks are found by walking the MRO for distinct method names, so give each
class its own method name. Reusing a subclass method name silently hides the
base hook.

```python
import asyncio

from lauren_di import DIContainer, injectable, post_construct, pre_destruct

order: list[str] = []


@injectable()
class Base:
    @post_construct
    def base_up(self) -> None:
        order.append("base.up")

    @pre_destruct
    def base_down(self) -> None:
        order.append("base.down")


@injectable()
class Child(Base):
    @post_construct
    def child_up(self) -> None:
        order.append("child.up")

    @pre_destruct
    def child_down(self) -> None:
        order.append("child.down")


async def main() -> None:
    container = DIContainer()
    container.register(Child)
    await container.startup()
    await container.resolve(Child)
    assert order == ["base.up", "child.up"]
    await container.shutdown()
    assert order == ["base.up", "child.up", "child.down", "base.down"]


asyncio.run(main())
```

`startup()` fires `@post_construct` for **every** scope. There is no framework
scheduler here: a singleton is constructed exactly once, so firing at
construction is firing exactly once. `@post_construct` on a `REQUEST` provider
fires once per scope, when that instance is created.

## Teardown order and error containment

`shutdown()` and `ResolutionScope.close()` tear down in **reverse creation
order** — the newest instance goes first. A failing `@pre_destruct` does not
abort the rest: each failure is collected into a `DestructError` and the
remaining instances are still torn down. Both calls return the errors.

```python
import asyncio

from lauren_di import DestructError, DIContainer, injectable, pre_destruct


@injectable()
class Fragile:
    @pre_destruct
    async def fail(self) -> None:
        raise RuntimeError("boom")


async def main() -> None:
    container = DIContainer()
    container.register(Fragile)
    await container.startup()
    await container.resolve(Fragile)
    errors = await container.shutdown()
    assert len(errors) == 1
    assert isinstance(errors[0], DestructError)


asyncio.run(main())
```

Inspect `DestructError.detail` for which hook failed and at which stage; the
instance is discarded either way.
