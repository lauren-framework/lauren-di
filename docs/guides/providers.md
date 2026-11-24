# Providers and injection

`@injectable()` is the only declaration the library has, and it has two shapes: a
class and a function. This page covers what the container reads from each, what it
refuses to read, and the error each refusal produces.

Related pages: [scopes](scopes.md) for lifetimes,
[custom providers and tokens](custom-providers.md) for the `use_class` /
`use_factory` recipes, and [errors](../errors.md) for the exception types.

## Which injection style

There are three ways to receive a dependency. They are not interchangeable: each one
is read at a different moment and fails differently.

| Style | What you write | Use it for | Trade-off |
|-------|----------------|------------|-----------|
| Constructor | `def __init__(self, repo: Repo) -> None:` | collaborators the object cannot work without | one parameter per dependency, and the only way to mark one optional is a default |
| Class-body field | `repo: Repo` | classes that would otherwise write `__init__` only to assign attributes | six gates; a field that fails one is dropped **silently** |
| `Annotated[T, Inject(token)]` | `dsn: Annotated[str, Inject("DSN")]` | tokens that are not classes: strings, integers, config keys, aliases | verbose, and the annotation stops describing the object once a token overrides it |

Default to the constructor: it is validated at `register` and `compile`, it keeps
the object immutable, and it is the only style whose failures name the parameter.
Reach for a class-body field when the class would otherwise carry an `__init__` that
does nothing but assign, and for `Inject` when the token you need is not a type.
The three compose — a class may take constructor dependencies, hold injected
fields, and use an injected token in one of those signatures.

## Constructor injection

The container calls `inspect.signature(cls)` and walks the parameters in order. It
does not read `__init__` directly, so a class with no `__init__`, a class with an
inherited one, and a class defining `__new__` all behave the same way.

| Parameter shape | Collected? | Token | What happens next |
|-----------------|------------|-------|-------------------|
| `self`, `cls` | No | — | — |
| `repo: Repo` | Yes | `Repo` | `MissingProviderError` at `compile` if nothing provides `Repo` |
| `repo: Repo = None` | Yes | `Repo` | the provider wins; the default is used only when no provider exists |
| `dsn: Annotated[str, Inject("DSN")]` | Yes | `"DSN"` | `MissingProviderError` at `compile` if `"DSN"` is unbound |
| `repo` (no annotation) | No | — | `UnresolvableParameterError` at `register` |
| `*args`, `**kwargs` | No | — | never filled by the container |
| `repo: Repo, /` (positional-only) | Yes | `Repo` | collected but never passed — `TypeError` at first instantiation |

A default does not switch injection off. `def __init__(self, clock: Clock = None)`
is still collected with token `Clock`, and a registered `Clock` is injected over the
default; the default is what you get when no provider exists. That is what makes a
default the idiom for an optional dependency.

An unannotated parameter is refused at `register`, before the graph exists:

<!-- docs-contract: fragment -->
```python
@injectable()
class Broken:
    def __init__(self, repo) -> None: ...
    # UnresolvableParameterError: Broken parameter 'repo' missing annotation
```

Positional-only parameters are the one shape that is collected and then cannot be
used. The container calls the class with keyword arguments, so a `/`-delimited
parameter is never filled. With `SINGLETON` scope the instance is built during
`startup()`, so that is where the `TypeError` surfaces — not at `register`, and not
at `resolve`:

<!-- docs-contract: fragment -->
```python
@injectable()
class PositionalOnly:
    def __init__(self, repo: Repo, /) -> None: ...
    # startup() -> TypeError: __init__() missing 1 required positional argument: 'repo'
```

`*args` and `**kwargs` are skipped, and they are not a catch-all: the container
filters the arguments it passes against the signature, so
`def __init__(self, repo: Repo, **kwargs)` receives the injected `repo` and an empty
`kwargs`. Injecting never produces a duplicate-keyword `TypeError`.

## Class-body field injection

A field is injected only if it passes **all six gates**. They run when the class is
registered, in this order:

1. the class was declared `@injectable()` — the decorator attaches the metadata the
   gates read, and an undecorated class is rejected before any gate runs;
2. the annotation resolved to a real object — a string forward reference that does
   not resolve is dropped;
3. the field has no class-body value (`clock: Clock = None` is dropped, not made
   optional the way a constructor default is);
4. the field appears in this class's own `__annotations__` — inherited annotations
   are not re-injected;
5. the annotation is not a dataclass field — both decorator orders are dropped;
6. the annotation passes the shape test: a user-defined class (decorated or not), a
   runtime-checkable `Protocol`, or `list[T]` over such a type.
   `Annotated[T, Inject(token)]` short-circuits the shape test and contributes
   `token`.

Anything that fails one of them is skipped by the gates below, silently: no error at
`register`, none at `compile`, and the attribute is simply missing the first time you
touch it. A function token is the case that catches people: the field
`session: Depends[make_session]` is dropped, because `Depends` evaluates to the
function and a function fails gate 6. The form the gates honour is
`Annotated[Session, Inject(make_session)]`.

| Field annotation | Collected? | Token |
|------------------|------------|-------|
| `clock: Clock` where `Clock` is `@injectable()` | Yes | `Clock` |
| `clock: Clock` where `Clock` is a plain class | Yes | `Clock` |
| `store: Store` where `Store` is a runtime-checkable `Protocol` | Yes | `Store` |
| `clocks: list[Clock]` | Yes | `list[Clock]` |
| `dsn: Annotated[str, Inject("DSN")]` | Yes | `"DSN"` |
| `count: int` | No | — |
| `tags: list[str]` | No | — |
| `limits: dict[str, int]` | No | — |
| `pair: tuple[Clock, ...]` | No | — |
| `count: ClassVar[int]` | No | — |
| `clock: Clock = None` | No | — |
| `clock: Clock` inherited from a base class | No | — |
| `clock: Clock` on a `@dataclass` | No | — |
| `count: Annotated[int, "meta"]` | No | — |
| `clock: Depends[Clock]` | Yes | `Clock` |
| `clock: Depends[make_clock]` where `make_clock` is a function | No | — |

The last two rows are the trap. `Depends[...]` is a runtime alias: it evaluates to
its argument before the gates ever see it, so `Depends[Clock]` *is* `Clock` and is
collected. `Depends[make_clock]` is a function, fails gate 6, and is dropped
silently — no error at `register`, none at `compile`, and the attribute is simply
absent when you first touch it. For a token that is not a class, write the
`Annotated[T, Inject(token)]` form, which the gates honour:

<!-- docs-contract: fragment -->
```python
@injectable()
class Repository:
    dsn: Annotated[str, Inject("DSN")]

    def __init__(self, pool: Pool) -> None:
        self.pool = pool  # constructor deps and fields live side by side
```

Fields are populated after construction, so a class keeps its own `__init__` and
gains the injected attributes on top. Because every gate drops rather than raises,
nothing warns you about a field that was skipped; if an attribute is missing at
first use, check the six gates before anything else.

## Function providers

A decorated function is a provider whose token is the function object itself.

| Declaration | `resolve` returns | Teardown |
|-------------|-------------------|----------|
| `def make_session(cfg: Config) -> Session` | the return value | — |
| `async def make_session(cfg: Config) -> Session` | the awaited value | — |
| `def session(cfg: Config)` with a `yield` | the yielded value | the code after `yield` runs at `shutdown()` |
| `async def session(cfg: Config)` with a `yield` | the yielded value | the code after `yield` is awaited at `shutdown()` |

A provider function's parameters are resolved with the same rules as a constructor's:
annotate them and they are injected, leave one unannotated and `register` refuses the
function. The body runs once for `SINGLETON` scope, and the teardown of generator
providers runs at `shutdown()` in reverse order of construction.

Because the token is the function, an annotation that asks for it has to name the
function, which means the `Annotated[T, Inject(fn)]` form:

<!-- docs-contract: fragment -->
```python
@injectable()
def make_session(cfg: Config) -> Session:
    return Session(cfg.dsn)


@injectable()
class Handler:
    def __init__(self, session: Annotated[Session, Inject(make_session)]) -> None:
        self.session = session
```

The `Depends[...]` alias resolves to the same object, so a constructor parameter can
name the function that way too; the field gates, as shown above, cannot.

## Classes as providers

`@injectable()` is mandatory, and it is a marker, not a wrapper: the decorator
returns the class object unchanged, so `isinstance` checks, subclassing and type
hints keep working.

* An undecorated class is refused at `register`:
  `MissingProviderError: Plain is not marked @injectable`.
* A missing provider is refused at `compile`, and the message names the consumer:
  `MissingProviderError: No provider for Dep (required by Svc.d)`.
* Injectable metadata is inherited and that inheritance is an error: subclassing an
  `@injectable()` class and registering the subclass raises
  `MetadataInheritanceError`. Decorate the subclass, which also re-reads its own
  field annotations — fields declared on the base are not injected again.
* A token does not have to be the class. `register_class(token, cls)` binds one, and
  `use_class(provide=..., use=...)` builds a `CustomProvider` that you install with
  `register_custom` — `register` rejects it with `InvalidProviderError`. Add
  `multi=True` to collect several implementations under one token and read them back
  with `resolve(list[Token])`, in registration order.

## Common mistakes

| Mistake | What you get |
|---------|--------------|
| Registering a class you forgot to decorate | `MissingProviderError: X is not marked @injectable`, at `register` |
| Registering a subclass of an `@injectable()` class | `MetadataInheritanceError`, at `register` |
| A constructor parameter with no annotation | `UnresolvableParameterError: ... parameter 'x' missing annotation`, at `register` |
| Forgetting the provider for an annotated dependency | `MissingProviderError: No provider for T (required by C.x)`, at `compile` |
| A positional-only dependency | collected, then `TypeError` at first instantiation (at `startup()` for `SINGLETON`) |
| `clock: Depends[make_clock]` on a class-body field | silently skipped; `AttributeError` when you first use the attribute |
| A dataclass field, an inherited field, or a field with a class-body default | silently skipped |
| `count: int` or `tags: list[str]` on a class body | silently skipped — primitives never pass the shape test |
| Passing `use_class(...)` to `register` | `InvalidProviderError`; use `register_custom` |
| Expecting `**kwargs` to receive the graph | `kwargs` stays empty; the container filters against the signature |

## One program, end to end

The three styles together, plus a token, a multi-binding and a generator teardown:

<!-- docs-contract: run -->
```python
import asyncio
from typing import Annotated, Protocol, runtime_checkable

from lauren_di import DIContainer, Inject, injectable, use_class


@injectable()
class Config:
    def __init__(self, dsn: Annotated[str, Inject("DSN")]) -> None:
        self.dsn = dsn


@injectable()
class Clock:
    def __init__(self, cfg: Config) -> None:
        self.dsn = cfg.dsn


@runtime_checkable
class Notifier(Protocol):
    def send(self, msg: str) -> str: ...


@injectable()
class Email:
    def send(self, msg: str) -> str:
        return f"email:{msg}"


@injectable()
class Sms:
    def send(self, msg: str) -> str:
        return f"sms:{msg}"


opened: list[str] = []


@injectable()
def session(cfg: Config):
    opened.append(f"open:{cfg.dsn}")
    yield {"conn": "ok"}
    opened.append("close")


@injectable()
class Greeter:
    clock: Clock  # field injection: no __init__ at all

    def greet(self) -> str:
        return f"hello from {self.clock.dsn}"


async def main() -> None:
    container = DIContainer()
    container.register_factory("DSN", lambda: "postgres://app")
    container.register(Config)
    container.register(Clock)
    container.register(Greeter)
    container.register(Email)
    container.register(Sms)
    container.register_custom(use_class(provide=Notifier, use=Email, multi=True))
    container.register_custom(use_class(provide=Notifier, use=Sms, multi=True))
    container.register(session)
    container.compile()
    await container.startup()

    greeter = await container.resolve(Greeter)
    assert greeter.greet() == "hello from postgres://app"

    notifiers = await container.resolve(list[Notifier])
    assert [n.send("hi") for n in notifiers] == ["email:hi", "sms:hi"]

    assert (await container.resolve(session))["conn"] == "ok"
    assert opened == ["open:postgres://app"]

    await container.shutdown()
    assert opened == ["open:postgres://app", "close"]


asyncio.run(main())
```

`Config` is constructor-injected with a string token, `Greeter` is field-injected,
`Notifier` is bound twice and read back as a list, and `session` is a generator
provider whose teardown only runs at `shutdown()`.

## Where to go next

- [Scopes](scopes.md) — when instances are built and destroyed.
- [Custom providers and tokens](custom-providers.md) — `use_value`, `use_class`,
  `use_factory`, `use_existing`, and multi-bindings.
- [Errors](../errors.md) — the exception hierarchy behind every message above.
- [Troubleshooting](../troubleshooting.md) — symptoms such as a missing attribute
  and the gate that dropped it.
