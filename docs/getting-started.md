# Getting started

This page is the first hour with `lauren_di`: install it, run the quickstart, learn the
four moves it makes, break a graph on purpose, then test one. Every Python block here is
compiled by the docs test suite and the runnable ones execute on every commit, so you can
paste them in order.

## Install

```bash
pip install lauren-di
```

| | |
|---|---|
| Python | 3.10 or newer |
| Runtime dependencies | none - standard library only |
| Typing | PEP 561: ships `py.typed`, so mypy and pyright see real signatures, no stubs |
| Extras | none needed to run it; the extras in `pyproject.toml` are for contributors |
| Async | `resolve()` is a coroutine, because providers may do async work; everything else is sync |

Two sentences are the whole mental model. You **register** a class or factory with a
container, the container **resolves** an object graph by reading type annotations, and a
**scope** decides how long each object lives and which provider may depend on which.
Everything below is a detail of those three verbs.

## Quickstart

Save this as `quickstart.py` and run `python quickstart.py`. It prints nothing: it
asserts, so a silent exit means the graph resolved.

<!-- docs-contract: run -->
```python
import asyncio
from lauren_di import DIContainer, Scope, injectable

@injectable()
class Config:
    dsn = "sqlite:///app.db"

@injectable()
class Session:
    def __init__(self, cfg: Config) -> None:      # constructor injection
        self.dsn = cfg.dsn

@injectable(scope=Scope.REQUEST)
class UserRepo:
    session: Session                              # class-body field injection

async def main() -> None:
    container = DIContainer()
    for cls in (Config, Session, UserRepo): container.register(cls)
    assert (await container.resolve(Config)).dsn == "sqlite:///app.db"   # singleton
    async with container.scope() as scope:
        repo = await scope.resolve(UserRepo)      # request-scoped
        assert repo.session.dsn == "sqlite:///app.db"

asyncio.run(main())
```

Reading it top to bottom:

- `@injectable()` marks a class as something the container may construct.
- `Config` asks for nothing. A plain class with a class attribute is a valid provider.
- `Session.__init__(self, cfg: Config)` asks for its dependency through the constructor.
- `UserRepo` asks with a bare class-attribute annotation, `session: Session`, and has no
  `__init__` of its own. Both spellings work; pick one per class.
- `container.register(...)` declares providers. Nothing is constructed yet.
- `await container.resolve(Config)` is the first resolution, so it also validates the
  graph.
- `async with container.scope()` opens a request scope. `scope.resolve(UserRepo)` builds
  `UserRepo`, then `Session`, then `Config`; leaving the block releases what the scope
  owned.
- `asyncio.run(main())` drives it, because resolution is async.

## The four things you just did

### Declaration

Annotate the dependencies; the container does the rest.

<!-- docs-contract: run -->
```python
@injectable()
class AuditLog:
    pass


@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    log: AuditLog
```

Skip it and registration fails immediately: registering a class with no `@injectable()`
raises `MissingProviderError: Plain is not marked @injectable`, with
`detail == {'token': 'Plain'}`.

### Registration

Registration binds a token to a provider. The token is usually the class itself.

<!-- docs-contract: run -->
```python
container = DIContainer()
for cls in (AuditLog, UnitOfWork):
    container.register(cls)
```

Skip it and resolution fails: `MissingProviderError: No provider for AuditLog`, with
`detail == {'token': 'AuditLog'}`. `register()` also accepts an explicit token or a
factory when the class is not its own key. See [providers](guides/providers.md).

### Validation

`compile()` checks the whole graph before anything is built: every dependency is
satisfiable and every scope assignment is legal.

<!-- docs-contract: run -->
```python
healthy = DIContainer()
healthy.register(AuditLog)
healthy.register(UnitOfWork)
healthy.compile()  # every dependency satisfiable, every scope legal
```

Skip it and you find out at the worst moment, in production, on the request that happens
to need the broken path. `compile()` turns both mistakes into startup failures:

```python
@injectable()
class RowSource:
    pass


@injectable()
class Report:
    def __init__(self, rows: RowSource) -> None: ...


orphan = DIContainer()
orphan.register(Report)  # nobody registered RowSource
orphan.compile()
# MissingProviderError: No provider for RowSource (required by Report.rows)
# detail: {'token': 'RowSource', 'dependent': 'Report', 'param': 'rows', 'hint': ...}
```

Cycles are caught the same way: `CircularDependencyError: Circular dependency: B -> C ->
B`, with `detail['cycle'] == ['B', 'C', 'B']`. You do not have to call `compile()` -
`resolve()` compiles on first use - but calling it once at startup is how you get the
error at startup.

### Resolution and scopes

A scope is the lifetime of everything request-scoped that was built inside it. The same
object comes back twice inside one scope, and a fresh one in the next.

<!-- docs-contract: run -->
```python
async def lifetimes() -> tuple[bool, bool]:
    async with container.scope() as one, container.scope() as two:
        same_in_scope = (await one.resolve(UnitOfWork)) is (await one.resolve(UnitOfWork))
        shared_across_scopes = (await one.resolve(UnitOfWork)) is (await two.resolve(UnitOfWork))
        return same_in_scope, shared_across_scopes


assert asyncio.run(lifetimes()) == (True, False)
```

Singletons such as `AuditLog` ignore scopes: you get the same instance everywhere. Skip
the scope and you get a provider that is never torn down - a warning, not an error:
``Req is request-scoped but was resolved outside a request or `app.scope()`; it will never
be torn down.`` `DIContainer(strict_request_scope=True)` promotes that warning to
`DIScopeViolationError`.
The other scope mistake is a lifetime inversion, a long-lived provider holding a
short-lived one:

```python
@injectable()
class Cached:
    def __init__(self, uow: UnitOfWork) -> None: ...


bad = DIContainer()
bad.register(AuditLog)
bad.register(UnitOfWork)
bad.register(Cached)
bad.compile()
# DIScopeViolationError: Singleton Cached depends on request-scoped UnitOfWork
# detail: {'dependent': 'Cached', 'dependent_scope': 'singleton',
#          'dependency': 'UnitOfWork', 'dependency_scope': 'request'}
```

The fix is to widen the dependency to a singleton, or to inject a factory that is called
inside the scope. [scopes](guides/scopes.md) covers the lifecycle rules in full.

## Your first error

Every failure is a `DIError` subclass with a stable `code` and an optional `detail`
mapping. These four are the ones you will hit first.

| Error | Message | Fix |
|---|---|---|
| `MissingProviderError` | `Plain is not marked @injectable` | add `@injectable()` to the class or register a factory |
| `MissingProviderError` | `No provider for RowSource (required by Report.rows)` | register the dependency, or bind a protocol to it |
| `CircularDependencyError` | `Circular dependency: B -> C -> B` | break the cycle with a factory or a setter, or merge the classes |
| `DIScopeViolationError` | `Singleton Cached depends on request-scoped UnitOfWork` | widen the dependency, or inject a factory |

The message names the two ends of the problem and `detail` carries the same information
as data, so you can assert on it in tests and log it in structured form. The full
catalogue, with `to_dict()` and the `code` of each class, is in [errors](errors.md).

## Your first test

Give each test its own container, and swap collaborators with an `OverridePlan` instead
of monkeypatching.

```python
import asyncio

import pytest

from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable


@injectable()
class Clock:
    def now(self) -> str:
        return "2024-01-01"


@injectable()
class Greeter:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def greet(self) -> str:
        return f"hello at {self.clock.now()}"


class FrozenClock:
    def now(self) -> str:
        return "1970-01-01"


@pytest.fixture()
def container() -> DIContainer:
    built = DIContainer()
    built.register(Clock)
    built.register(Greeter)
    built.compile()
    return built


def greet(container: DIContainer) -> str:
    async def call() -> str:
        return (await container.resolve(Greeter)).greet()

    return asyncio.run(call())


def test_greeter_uses_the_real_clock(container: DIContainer) -> None:
    assert greet(container) == "hello at 2024-01-01"


def test_greeter_uses_the_frozen_clock() -> None:
    plan = OverridePlan(providers={Clock: ProviderOverride(Clock, "value", value=FrozenClock())})
    container = DIContainer(overrides=plan)
    for cls in (Clock, Greeter):
        container.register(cls)
    container.compile()
    assert greet(container) == "hello at 1970-01-01"
```

Two properties make this worth the setup: the fixture is per test, so no singleton leaks
between tests, and the override is a declaration rather than a patch, so `compile()` still
validates the graph. [testing](guides/testing.md) has the pytest plugin story and async
fixtures.

## Where to go next

| Page | Read it for |
|---|---|
| [providers](guides/providers.md) | factories, explicit tokens, multi-bindings, protocol binding |
| [scopes](guides/scopes.md) | request and app scopes, teardown order, lifetime rules |
| [custom providers](guides/custom-providers.md) | writing your own provider kind |
| [testing](guides/testing.md) | fixtures, overrides, async tests |
| [recipes](recipes.md) | configuration from a dict, opening a resource once, wiring a fake |
| [troubleshooting](troubleshooting.md) | symptom to cause to fix, for the errors above |
| [concurrency](concurrency.md) | sharing a container across tasks, scope-per-task |
| [design](design.md) | why resolution is async, how compile works, invariants |
| [errors](errors.md) | every error class, its `code` and `detail` keys |
| [reference](reference.md) | every public name and signature |
| [examples](https://github.com/lauren-framework/lauren-di/blob/main/examples/README.md) | runnable scripts, `01_minimal_singleton.py` onwards |
