# Scopes and lifecycle

## The three rules to remember

1. **A consumer may only depend on something that lives at least as long as it
   does.** `TRANSIENT < REQUEST < SINGLETON`; an edge that points the other way
   is refused at `compile()` with `di_scope_violation`.
2. **A scope caches one instance per provider, for a single resolution
   sequence.** That is a sequencing rule, not a concurrency guarantee —
   concurrent resolution is not de-duplicated, see
   [concurrency](../concurrency.md).
3. **A request-scoped instance resolved with no open scope is never torn
   down.** Lax mode warns once per token; `strict_request_scope=True` refuses
   instead.

## The three scopes

`Scope` is an ordered `IntEnum`: `TRANSIENT = 0`, `REQUEST = 1`,
`SINGLETON = 2`. The numeric order is load-bearing — the compiler compares the
two values directly, so "wider" always means "higher number".

| Scope | Lives in | Reused | Torn down |
| --- | --- | --- | --- |
| `TRANSIENT` | nowhere | never — a fresh instance per resolution | never; the caller owns it |
| `REQUEST` | the active `ResolutionScope`'s cache | once per scope, per resolution sequence | when that scope closes |
| `SINGLETON` | the container | once per container | at `shutdown()` |

`TRANSIENT` and `SINGLETON` resolve on a bare container. `REQUEST` needs a
cache to live in, which is what `container.scope()` provides.

## The narrowing rule

An edge is legal when the dependency's scope is **greater than or equal to**
the consumer's scope. The dependency has to outlive, or exactly match, the
lifetime of the object holding it.

The rule exists because the alternative is a lifetime bug that no test
catches: a `SINGLETON` consumer of a `TRANSIENT` dependency would capture one
per-resolution object and keep it for the life of the process, and a `REQUEST`
consumer of a `TRANSIENT` dependency would silently cache a value that was
supposed to be per-resolution. Both are refused rather than "fixed up"
silently.

## The 3x3 matrix

Rows are consumers, columns are dependencies.

| consumer ↓ / dependency → | `TRANSIENT` | `REQUEST` | `SINGLETON` |
| --- | --- | --- | --- |
| `TRANSIENT` | allowed | allowed | allowed |
| `REQUEST` | **refused** — `di_scope_violation` | allowed | allowed |
| `SINGLETON` | **refused** — `di_scope_violation` | **refused** — `di_scope_violation` | allowed |

Every refused cell raises the same class (`DIScopeViolationError`) with the
same `code` (`di_scope_violation`); only the `detail` payload and the message
differ:

| Refused edge | Message | `detail["dependent_scope"]` | `detail["dependency_scope"]` |
| --- | --- | --- | --- |
| `REQUEST` ← `TRANSIENT` | `Request SessionStore depends on transient-scoped DbSession` | `request` | `transient` |
| `SINGLETON` ← `TRANSIENT` | `Singleton ReportStore depends on transient-scoped DbSession` | `singleton` | `transient` |
| `SINGLETON` ← `REQUEST` | `Singleton ReportStore depends on request-scoped Session` | `singleton` | `request` |

All three are detected when the graph is compiled, before any instance is
built. The allowed cells are not all equivalent in behaviour, even though all
six compile:

- a `TRANSIENT` consumer of anything gets a fresh instance per resolution, and
  its dependency is resolved again each time;
- a `REQUEST` consumer of a `REQUEST` dependency shares that dependency with
  the rest of the scope;
- a `SINGLETON` consumer of a `SINGLETON` dependency shares the container-wide
  instance, and the dependency is built first.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, DIScopeViolationError, Scope, injectable


@injectable(scope=Scope.TRANSIENT)
class DbSession:
    """Cheap per-resolution handle."""


@injectable(scope=Scope.SINGLETON)
class ReportStore:
    db: DbSession


container = DIContainer()
container.register(DbSession)
container.register(ReportStore)

try:
    container.compile()
except DIScopeViolationError as exc:
    assert exc.code == "di_scope_violation"
    assert exc.detail["dependent"] == "ReportStore"
    assert exc.detail["dependent_scope"] == "singleton"
    assert exc.detail["dependency_scope"] == "transient"
    assert str(exc) == "Singleton ReportStore depends on transient-scoped DbSession"
```

Widen the dependency and the same graph compiles:

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.SINGLETON)
class DbSession:
    """Container-wide connection pool."""


@injectable(scope=Scope.SINGLETON)
class ReportStore:
    db: DbSession


container = DIContainer()
container.register(DbSession)
container.register(ReportStore)
container.compile()  # ok: singleton <- singleton
```

## Reading a scope violation

`DIScopeViolationError` carries a `detail` mapping that is stable across
messages, so assert on keys rather than on text:

| Key | Value |
| --- | --- |
| `dependent` | class name of the consumer |
| `dependent_scope` | `"transient"`, `"request"` or `"singleton"` |
| `dependency_scope` | scope of the thing being injected |
| `dependency` | class name of the dependency |
| `alias` | present only when the edge was declared through an alias |

The `code` is `di_scope_violation`; see [errors](../errors.md) for the full
code table.

## Owning a request cache

`container.scope()` opens a `ResolutionScope`. It is an async context manager
and is also awaitable-free to close explicitly; leaving the block finalises
everything created inside it.

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, LifecycleViolationError, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


async def main() -> None:
    container = DIContainer()
    container.register(Session)

    async with container.scope(values={str: "client.example"}) as scope:
        session = await scope.resolve(Session)
        assert await scope.resolve(Session) is session  # one instance, one sequence
        assert await scope.resolve(str) == "client.example"
        assert scope.values == {str: "client.example"}
        assert scope.closed is False

    assert scope.closed is True
    assert scope.errors == ()
    assert await scope.close() == ()  # idempotent

    try:
        await scope.resolve(Session)
    except LifecycleViolationError as exc:
        assert exc.code == "lifecycle_violation"
        assert exc.detail == {"token": "Session", "origin": "scope", "state": "closed"}


asyncio.run(main())
```

What the scope owns:

| Member | Meaning |
| --- | --- |
| `resolve(token)` | resolve through the container, caching `REQUEST` results here |
| `values` | extra per-scope values passed to `container.scope(values=...)` |
| `closed` | `True` once the scope has been finalised |
| `errors` | tuple of teardown failures collected while closing |
| `close()` | finalise now; returns the same tuple as `errors` |

`values` is a per-scope overlay: it is resolvable like a registration but is
never written back to the container, so two scopes opened on the same
container can carry different values.

## What the unit of work is, per host

A web host answers this for you: the container is the app object, the framework
hands the adapter a per-request hook, and the request is the unit of work. A CLI
host has neither the hook nor the request, so **the process is the container
lifetime and the command body is the unit of work** `[H-20, H-21]`. Three
consequences follow. Build the container inside the entry point, after the parser
has bound its arguments and never at import time (`A-1`) — an import-time
container is shared by every command in the process and outlives the loop its
singletons were built on. Call `await container.startup()` before the body and
`await container.shutdown()` in a `finally` after it (`A-2`), because the body
raises, the parser exits and the user hits Ctrl-C, and none of those may skip
teardown. And open exactly one scope around the body: a `REQUEST` provider
resolved with no scope open is built, cached nowhere and never finalised —
`shutdown()` still returns `()` and its `pre_destruct` never runs `[H-20]` —
while the same token inside `async with container.scope()` is one instance for
the whole invocation, torn down when the scope closes, before the process exits
`[H-21]`. Reusing one container across two invocations is safe only when every
provider is loop-free (`A-12`): a singleton built while the first invocation's
loop was running keeps a reference to that loop, and the second invocation gets a
new one. [cli](cli.md) has the five recipes.

## Strict vs lax request-scope mode

The container does not refuse a `REQUEST` resolution just because no scope is
open — that would break module-level code and tests. Instead the default
(**lax**) mode builds an instance, does not record it for teardown, and logs
one warning per token:

```
Session is request-scoped but was resolved outside a request or `app.scope()`; it will never be torn down. Use `app.scope()` or enable `strict_request_scope=True`.
```

The warning fires once per provider token per container, so a hot path does
not flood the log; the *instances* are not cached, so each such resolution
constructs a fresh one and none of them are ever finalised.

`strict_request_scope=True` turns the same situation into an error, which is
what you want in production services:

| Mode | Outside a scope | Inside a scope |
| --- | --- | --- |
| lax (default) | builds an instance, warns once, no teardown | cached in the scope, finalised on close |
| `strict_request_scope=True` | `DIScopeViolationError` / `di_scope_violation` | cached in the scope, finalised on close |

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, DIScopeViolationError, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


async def main() -> None:
    lax = DIContainer()
    lax.register(Session)
    first = await lax.resolve(Session)  # logs the warning above
    second = await lax.resolve(Session)
    assert first is not second  # nothing caches it, nothing tears it down

    strict = DIContainer(strict_request_scope=True)
    strict.register(Session)
    try:
        await strict.resolve(Session)
    except DIScopeViolationError as exc:
        assert exc.code == "di_scope_violation"
        assert exc.detail["reason"] == "no_active_scope"

    async with strict.scope() as scope:
        assert await scope.resolve(Session) is await scope.resolve(Session)


asyncio.run(main())
```

Strict mode never affects resolution *inside* a scope: `detail["reason"]` is
always `"no_active_scope"`, so a strict failure always means "you forgot to
open a scope", never "the graph is wrong".

## Nested scopes

A scope opens a child with `scoped.scope()`, and a child can open its own
child, to any depth. The difference from calling `container.scope()` again is
what the child *sees*:

| Thing | Inherited by a child scope? |
| --- | --- |
| `SINGLETON` instances | yes — they live on the container |
| `REQUEST` instances of the ancestors | yes, read through — the instance the parent created is reused, not rebuilt |
| `REQUEST` instances the child creates | no — cached at the child and torn down when the child closes |
| parent's `values` | yes — copied into the child, which may override them |

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


@injectable(scope=Scope.SINGLETON)
class Settings:
    pass


async def main() -> None:
    container = DIContainer()
    container.register(Session)
    container.register(Settings)

    async with container.scope(values={"tenant": "acme"}) as outer:
        outer_session = await outer.resolve(Session)
        settings = await outer.resolve(Settings)

        async with outer.scope(values={"job": "nightly"}) as inner:
            assert inner.depth == 2
            assert inner.parent is outer
            assert inner.values == {"tenant": "acme", "job": "nightly"}
            assert await inner.resolve(Session) is outer_session  # read through
            assert await inner.resolve(Settings) is settings

        assert inner.closed is True
        assert outer.closed is False
        assert await outer.resolve(Session) is outer_session


asyncio.run(main())
```

Closing a scope finalises the instances it created, newest first, and closes any
child scopes nobody closed first — so an instance is never handed out after the
scope that owns it tore it down. Each level keeps its own `errors` tuple.

`container.scope()` inside an open scope is still a **root**: a second chain
with an empty cache and no inherited values. Reach for it when the nested work
must not share request state at all.

### Capping the depth

Nesting is unlimited by default. When a graph or a host wants a bound, state it
once on the container — or narrow it for one subtree:

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, LifecycleViolationError, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


async def main() -> None:
    container = DIContainer(max_scope_depth=2)
    container.register(Session)

    root = container.scope()
    child = root.scope()

    try:
        child.scope()
    except LifecycleViolationError as exc:
        assert exc.detail["reason"] == "depth_exceeded"
        assert exc.detail["depth"] == 3
        assert exc.detail["max_depth"] == 2

    await root.close()


asyncio.run(main())
```

`max_depth=` on `container.scope()` or `scoped.scope()` narrows the limit for
that subtree; it cannot widen it, and `max_depth=0` is a
`LifecycleConfigError`. The limit is checked *before* anything is constructed,
so an over-deep call costs nothing.

## Aliases are transparent to scope validation

An alias registered with `container.register_alias(token, existing)` has no
scope of its own. The compiler follows the alias to its terminal target and
judges the edge by *that* provider's scope, then records the alias in the
detail so the error still names what you wrote.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, DIScopeViolationError, Scope, injectable


class SessionPort:
    """Interface token; the concrete class is bound by alias."""


@injectable(scope=Scope.REQUEST)
class Session:
    pass


@injectable(scope=Scope.SINGLETON)
class SessionStore:
    port: SessionPort


container = DIContainer()
container.register(Session)
container.register_alias(SessionPort, Session)
container.register(SessionStore)

try:
    container.compile()
except DIScopeViolationError as exc:
    assert exc.code == "di_scope_violation"
    assert exc.detail["dependency_scope"] == "request"
    assert exc.detail["alias"] == "SessionPort"
```

Aliases that do not terminate cleanly fail earlier, with their own codes:

| Situation | Error | `code` |
| --- | --- | --- |
| alias points at something unregistered | `MissingProviderError` | `missing_provider` |
| aliases form a loop (`A -> B -> A`) | `CircularDependencyError` | `circular_dependency` |

An alias to a `TRANSIENT` provider is refused from a `REQUEST` or `SINGLETON`
consumer for the same reason the direct edge is refused — the alias adds no
leniency.

## Using a closed scope

A `ResolutionScope` is single-use. After `close()` (or after the `async with`
block exits), every resolution through it raises `LifecycleViolationError`
with `code == "lifecycle_violation"` and
`detail == {"token": ..., "origin": "scope", "state": "closed"}` — including
`TRANSIENT` tokens and `values` entries, which never needed the cache.

`close()` is idempotent and does not raise for a failing finaliser: failures
are collected into the tuple it returns, which is the same tuple as
`scope.errors`. Resolve through a fresh scope, or through the container, after
a scope is gone.

## Lifecycle hooks

Finalisation is driven by two decorators, `post_construct` and `pre_destruct`.
They run in dependency order on the way up and in reverse on the way down.

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, Scope, injectable, post_construct, pre_destruct

order: list[str] = []


@injectable(scope=Scope.SINGLETON)
class Base:
    @post_construct
    def up(self) -> None:
        order.append("base.up")

    @pre_destruct
    def down(self) -> None:
        order.append("base.down")


@injectable(scope=Scope.SINGLETON)
class Child:
    base: Base

    @post_construct
    def up(self) -> None:
        order.append("child.up")

    @pre_destruct
    def down(self) -> None:
        order.append("child.down")


async def main() -> None:
    container = DIContainer()
    container.register(Base)
    container.register(Child)

    await container.startup()
    assert order == ["base.up", "child.up"]

    await container.shutdown()
    assert order == ["base.up", "child.up", "child.down", "base.down"]


asyncio.run(main())
```

The same hooks run for request-scoped providers, but at scope close instead of
container shutdown — that is the whole point of owning a scope.

## Teardown order and error containment

Teardown is strict LIFO over the resolution order: the most recently created
instance is finalised first, so a consumer always outlives its dependencies.

| Boundary | Created | Finalised |
| --- | --- | --- |
| `container.startup()` / `shutdown()` | singletons, dependency-first | reverse creation order |
| `scope.close()` | request instances, dependency-first | reverse creation order |

A `pre_destruct` that raises does not abort the rest of the teardown: the
failure is collected, teardown continues, and the collected failures are
returned by `close()` (container shutdown and scope close behave the same).
`scope.errors` exposes them after the fact; the wrapped exception type is
`DestructError`.

Because a failing finaliser cannot roll back the instances already built,
treat `errors` as a signal to log and, in tests, to fail loudly — see
[troubleshooting](../troubleshooting.md).

## What the cache does not promise

`REQUEST` caching is scoped to one resolution sequence. Two coroutines that
resolve the same request-scoped provider through the same `ResolutionScope`
concurrently through an async factory can each build an instance; two
concurrent container-level resolutions of one singleton through an async
factory can do the same. Both limits are measured, with numbers, in
[concurrency](../concurrency.md). Do not read "one instance per scope" as a
thread-safety or de-duplication guarantee.

## See also

- [providers](providers.md) — the registration kinds that produce these scopes.
- [errors](../errors.md) — codes and `detail` payloads.
- [concurrency](../concurrency.md) — what the caches do and do not guarantee
  under parallel resolution.
- [troubleshooting](../troubleshooting.md) — diagnosing leaked or
  never-torn-down instances.
- [cli](cli.md) — the CLI recipes whose one container per process these
  lifetimes produce.
