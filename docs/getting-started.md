# Getting started

## Install

```bash
pip install lauren-di
```

Nothing else is needed: the library is standard library only, ships `py.typed`, and
has no runtime dependencies. The `dev`, `release` and `docs` extras exist for
contributors and are never required by a user.

## Quickstart

The twenty-line program below is the whole library in one screen. It declares three
providers, registers them, resolves a singleton and a request-scoped instance, and
asserts both values, so a paste that runs is a paste that works.

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

`Config` and `Session` are singletons: resolving `Config` twice hands back the same
object, and `Session` reads that one `dsn` through its constructor. `UserRepo` is
`Scope.REQUEST`, so it exists only inside `async with container.scope()`: every scope
builds its own instance over the shared `Session` and tears it down on exit. Nothing
is discovered by import side effects — the `for cls in ...` loop is the registration.

## What the snippet shows

### Declaration

`@injectable()` marks a class (or a function) as something the container may
construct. Dependencies are read from the constructor signature and from bare
class-body annotations:

```python
@injectable()
class Config:
    dsn = "sqlite:///app.db"

@injectable(scope=Scope.REQUEST)
class UserRepo:
    session: Session                              # class-body field injection
```

Class-body field injection is deliberately narrow. The annotation is collected only
when it has **no default value**, is **not** a dataclass field, and names a class the
container knows how to build. If you want a default, pass the dependency through
`__init__` instead — a default wins and the field is never injected.

### Registration

```python
container = DIContainer()
for cls in (Config, Session, UserRepo):
    container.register(cls)
```

`register` returns the immutable `Provider` row it created, so you can inspect what
was recorded. Registration is cheap; validation is not run until you ask for it.

### Validation

```python
container.compile()
```

`compile()` walks every edge and raises `CircularDependencyError`,
`MissingProviderError`, `ProtocolAmbiguityError`, `DIScopeViolationError` or
`UnresolvableParameterError`. Run it at startup, not on a request path.

### Resolution and scopes

```python
assert (await container.resolve(Config)).dsn == "sqlite:///app.db"   # singleton
async with container.scope() as scope:
    repo = await scope.resolve(UserRepo)      # request-scoped
```

Resolution is asynchronous because construction may be: `__init__` can be sync, but
providers, factories and lifecycle hooks may all await. `startup()` fires
`@post_construct` hooks base-classes-first; `shutdown()` tears everything down in
reverse creation order.

## Zero configuration

You do not have to call `compile()` or `startup()`. `resolve()` validates lazily on
first use if you skip `compile()`, and constructs on demand. What you lose is the
guarantee that a broken graph fails **before** traffic arrives, which is the only
reason the explicit calls exist.

## Next

Scopes are where a DI container stops being obvious. Read
[Scopes and lifecycle](guides/scopes.md) before you rely on `Scope.REQUEST`, and
[Providers and injection](guides/providers.md) for the full rules on what the
container reads from a constructor, a class body or a function.
