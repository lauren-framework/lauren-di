# Custom providers and tokens

Not everything you inject is a class you can annotate. `lauren_di` covers the
four NestJS-style recipes plus a first-class token type.

## `Token`

A `Token` is an opaque, hashable, debuggable identifier for a binding that is
not a type.

```python
from lauren_di import Token

DB_URL = Token("db_url")
assert DB_URL.name == "db_url"

# Tokens are unique by identity unless you opt out.
assert Token("x") != Token("x")
assert Token("x", unique=False) == Token("x", unique=False)
```

`unique=False` makes two tokens with the same name compare equal, which is what
you want when two modules must agree on a key without importing a shared
constant first. It is opt-in because accidental collisions are silent.

## `Inject` — spelling a non-type token in an annotation

`Inject(token)` is `Annotated` metadata. It never appears at the top level of a
parameter; it is always the second element of `Annotated[T, ...]`.

```python
from typing import Annotated

from lauren_di import DIContainer, Inject, Token, injectable, use_value

DB_URL = Token("db_url")


@injectable()
class Client:
    def __init__(self, url: Annotated[str, Inject(DB_URL)]) -> None:
        self.url = url


container = DIContainer()
container.register_custom(use_value(provide=DB_URL, value="postgres://x"))
container.register(Client)
```

Class-body fields work the same way:

```python
from typing import Annotated

from lauren_di import Inject, Token, injectable

DB_URL = Token("db_url")


@injectable()
class Repo:
    url: Annotated[str, Inject(DB_URL)]
```

!!! warning "A default value disables injection"
    `def __init__(self, url=Inject(DB_URL))` does **not** inject. The container
    sees a parameter with a default and leaves it alone. `Inject` must sit
    inside `Annotated`.

## `Depends` — a spelling alias

`Depends[T]` evaluates to `T`. There is no marker object and no runtime
behaviour; it exists for readers coming from FastAPI.

```python
from lauren_di import Depends


class Repo:
    pass


assert Depends[Repo] is Repo
```

## The four recipes

Each helper returns a frozen `CustomProvider` record. You hand it to
`container.register_custom(...)`, which lowers it into a normal `Provider` row.

### `use_value` — an already-built object

```python
from lauren_di import DIContainer, use_value

container = DIContainer()
container.register_custom(use_value(provide="answer", value=42))
```

### `use_class` — bind a token to a class

```python
from lauren_di import DIContainer, Scope, use_class


class Greeter:
    def greet(self) -> str:
        return "hi"


container = DIContainer()
container.register_custom(use_class(provide="greeter", use=Greeter, scope=Scope.TRANSIENT))
```

### `use_factory` — call a callable with injected arguments

```python
from lauren_di import DIContainer, use_factory

container = DIContainer()
container.register_custom(
    use_factory(provide="greeter", factory=lambda: "hi", injects=())
)
```

`injects` is the positional argument list, resolved left to right. Wrap an entry
in `OptionalDep(token)` to receive `None` instead of a `MissingProviderError`
when nothing is bound:

```python
from lauren_di import DIContainer, OptionalDep, use_factory


class Clock:
    pass


def build(clock, logger=None):
    return (clock, logger)


container = DIContainer()
container.register_class(Clock, Clock)
container.register_custom(
    use_factory(provide="built", factory=build, injects=(Clock, OptionalDep("LOGGER")))
)
```

`OptionalDep` is only meaningful inside an `injects` list. It is not an
annotation and cannot be used in `Annotated`.

### `use_existing` — an alias to another token

```python
from lauren_di import DIContainer, use_existing


class Greeter:
    pass


container = DIContainer()
container.register(Greeter)
container.register_custom(use_existing(provide="greeter", existing=Greeter))
```

Aliases chain, and `compile()` rejects a chain that loops back on itself with
`CircularDependencyError`. Self-aliasing (`token` → `token`) is rejected at
registration time.

## `CustomProvider`

The record every recipe lowers into. Useful when you build one yourself or
assert on it in a test.

```python
from lauren_di import CustomProvider, Scope

provider = CustomProvider(provide="answer", kind="value", value=42, scope=Scope.SINGLETON)
assert provider.kind == "value"
assert provider.multi is False
```

| field | meaning |
|---|---|
| `provide` | the lookup key |
| `kind` | `"value"`, `"class"`, `"factory"`, `"existing"` |
| `value` / `use_class` / `factory` | the payload for that kind |
| `inject` | positional tokens for `factory` |
| `existing` | the alias target for `"existing"` |
| `scope` | lifetime, default `SINGLETON` |
| `multi` | collect into `list[T]` instead of replacing |
| `name` | human-readable label used in messages |

## Multi-bindings

`multi=True` accumulates rows under one token. Resolving the token returns the
**last** row, while resolving `list[T]` returns all of them in registration
order.

```python
import asyncio

from lauren_di import DIContainer, injectable


@injectable(multi=True)
class Plugin:
    def __init__(self, name: str = "p") -> None:
        self.name = name


async def main() -> None:
    container = DIContainer()
    container.register(Plugin)
    plugins = await container.resolve(list[Plugin])
    assert len(plugins) == 1


asyncio.run(main())
```

Binding a non-multi token twice raises `DuplicateBindingError` at registration
time. Binding a `list[T]` that has no `multi` rows resolves to `[]`.
