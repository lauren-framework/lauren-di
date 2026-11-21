# Providers and injection

`@injectable()` is the only declaration the library has, and it has two shapes: a
class and a function. This page covers what the container reads from each, what it
refuses to read, and how a provider's token is decided.

Every block here is real code: `nox -s docs_contract` compiles all of them and runs
the one marked `run`.

## Constructor injection

Dependencies come from `inspect.signature(cls)` — the *class* as a callable, not
`cls.__init__` directly. That single choice is what makes `__signature__`-publishing
models, metaclass `__call__` factories and `__new__`-heavy classes work without
special cases.

```python
from lauren_di import DIContainer, injectable


@injectable()
class Repo:
    def all(self) -> list[str]:
        return ["a", "b"]


@injectable()
class ReportService:
    def __init__(self, repo: Repo, limit: int = 100) -> None:
        self.repo = repo
        self.limit = limit


container = DIContainer()
container.register(Repo)
container.register(ReportService)
```

| Parameter shape | Collected? | Token | If no provider exists |
|-----------------|------------|-------|-----------------------|
| `self`, `cls` | No | — | — |
| `*args`, `**kwargs` | No | — | — |
| no annotation, no default | **No — registration fails** | — | `UnresolvableParameterError` at `register` |
| no annotation, with default | No | — | the default is used |
| `x: T` | Yes | `T` | `MissingProviderError` at compile/resolve |
| `x: T = default` | Yes | `T` | the default is used (optional dependency) |
| `x: Annotated[T, Inject(tok)]` | Yes | `tok` (any hashable) | `MissingProviderError` |
| `def __init__(self, x: T, /)` | **No — registration fails** | — | `UnresolvableParameterError` |

Two of those rows are refusals rather than fallbacks, because both would fail late
and name the wrong problem:

```python
from lauren_di import injectable


# Unannotated: the container has no type to resolve by.
@injectable()
class Broken:
    def __init__(self, repo) -> None: ...
```

`Broken` raises `UnresolvableParameterError` at `register`, not `TypeError` at the
first call. The same is true of a positional-only dependency: the container injects
by **keyword**, so a value collected for `def __init__(self, repo: Repo, /)` could
never bind, and the target is rejected instead.

Keyword-only parameters (`def __init__(self, *, d: Dep)`) are collected normally,
and a `**kwargs` parameter receives everything — the container filters injected
arguments against the signature, so a namespace clash never raises `TypeError`.

## Class-body field injection

Field injection exists so a plain class can receive collaborators without writing
`__init__`. It is deliberately narrow: **most annotations are data, not
dependencies.**

```python
from lauren_di import injectable


@injectable()
class Logger:
    def write(self, line: str) -> None:
        print(line)


@injectable()
class AuditService:
    logger: Logger              # injected: no default, injectable type
    name: str = "audit"         # NOT injected: class-body default
    count: int                  # NOT injected: primitive type
```

A bare annotation is collected only when all of these hold:

1. the name is in the class's **own** annotations (a subclass does not inherit field
   dependencies);
2. it has **no value** in the class body — neither a literal nor a `dataclass`
   `field(...)`;
3. it is **not a dataclass field**, even when the dataclass field has no default;
4. its annotation **resolves** to something other than a `str`;
5. `Annotated[T, Inject(tok)]` short-circuits the shape test and contributes `tok`;
6. the annotation **looks injectable**: an `@injectable` class, a runtime-checkable
   `Protocol`, `list[T]` over an injectable `T`, or any other user-defined class.

Primitives (`str`, `int`, `bool`, `bytes`, `float`, `dict`, `tuple`, `set`, …) and
parameterised generics other than `list[T]` fail gate 6 and are skipped **silently** —
resolving them is always a bug, so the attribute is left for the class to set.

The last clause of gate 6 is the useful one: an unknown user class is collected, so a
missing provider surfaces as `MissingProviderError` during validation rather than as
an attribute that silently stays unset.

## Function providers

Any decorated non-class callable becomes a provider whose **token is the function
object**:

```python
from lauren_di import Depends, injectable


@injectable()
class Config:
    dsn = "sqlite:///app.db"


@injectable()
class Session:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn


@injectable()
def make_session(cfg: Config) -> Session:
    return Session(cfg.dsn)         # the return value IS the dependency


@injectable()
class UserRepo:
    session: Depends[make_session]  # depend on the factory by reference
```

- The function's **parameters** are its dependencies, under the same rules as a
  constructor; its **return annotation** is documentation and typing only.
- The function's own token is the function, so `await container.resolve(make_session)`
  works and `Depends[make_session]` is the idiomatic way to depend on it.
- The return value is the dependency. An `async def` factory is awaited.
- Functions carry no lifecycle hooks: `post_construct`/`pre_destruct` stay empty.

## Classes as providers

`register(SomeClass)` is the shorthand; `use_class` is the same thing spelled as a
custom provider, which is how you bind a token to an implementation and choose its
scope:

```python
from lauren_di import DIContainer, Scope, Token, injectable, use_class

Notifier = Token("Notifier")


@injectable()
class EmailNotifier:
    def send(self, text: str) -> None:
        print(text)


container = DIContainer()
container.register(use_class(provide=Notifier, use=EmailNotifier, scope=Scope.SINGLETON))
```

The recipes themselves — `use_value`, `use_class`, `use_factory`, `use_existing` —
and `list[T]` multi-bindings are covered in
[Custom providers and tokens](custom-providers.md).

## One program, end to end

```python run
import asyncio

from lauren_di import DIContainer, injectable


@injectable()
class Repo:
    def all(self) -> list[str]:
        return ["a", "b"]


@injectable()
def make_counter() -> int:
    return 0


@injectable()
class Service:
    repo: Repo                              # field injection
    counter: int                            # NOT injected: primitive type

    def __init__(self, repo: Repo) -> None:  # constructor injection
        self.repo = repo
        self.counter = 0

    def count(self) -> int:
        return len(self.repo.all())


async def main() -> None:
    container = DIContainer()
    container.register(Repo)
    container.register(make_counter)
    container.register(Service)
    container.compile()
    await container.startup()

    service = await container.resolve(Service)
    assert service.count() == 2
    assert (await container.resolve(make_counter)) == 0

    await container.shutdown()


asyncio.run(main())
```

## Where to go next

- [Scopes and lifecycle](scopes.md) — what `Scope` changes about all of the above.
- [Custom providers and tokens](custom-providers.md) — the four `use_*` recipes.
- [Testing and overrides](testing.md) — substituting a provider per build.
