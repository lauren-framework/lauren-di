# Custom providers and tokens

Not everything you inject is a class you can annotate. `lauren_di` covers the
four NestJS-style recipes plus a first-class token type.

## `Token`

A `Token` is an opaque, hashable key for a binding that has no type to annotate.

Tokens match by **identity**, not by name: two `Token("db")` calls are two
unrelated keys, and the string is a label for errors and `explain`, not a match key.

| form | matches | hash | use it when |
| --- | --- | --- | --- |
| `Token("db")` | that object only | `id()` | one module owns the binding |
| `Token("db", unique=False)` | any instance with that name | the name | two modules must agree on a key |

<!-- docs-contract: run -->
```python
from lauren_di import Token

assert Token("db") != Token("db")
assert Token("db", unique=False) == Token("db", unique=False)
assert repr(Token("db")) == "Token('db', unique)"
```

`unique=False` is the opt-out: those tokens compare and hash by name, so an
independently built instance is the same key. That makes the name load-bearing — a
typo is a different binding, and two unrelated `Token("settings", unique=False)`
collide — so keep it for seams you do not own (plugins, test helpers, generated
code) and share a module-level constant everywhere else.

## `Inject` — spelling a non-type token in an annotation

Resolution defaults to the parameter's type. When the key is not a type, put
`Inject(token)` inside `Annotated[...]`, on an `__init__` parameter or a class field.

<!-- docs-contract: run -->
```python
from typing import Annotated

from lauren_di import DIContainer, Inject, Token, injectable, use_value

DB_URL = Token("db_url")

@injectable()
class Repo:
    url: Annotated[str, Inject(DB_URL)]

@injectable()
class Client:
    def __init__(self, url: Annotated[str, Inject(DB_URL)]) -> None:
        self.url = url

container = DIContainer()
container.register_custom(use_value(provide=DB_URL, value="postgres://x"))
container.register(Repo)
container.register(Client)
container.compile()

assert (await container.resolve(Repo)).url == "postgres://x"
assert (await container.resolve(Client)).url == "postgres://x"
```

!!! warning "A default value disables injection"
    `def __init__(self, url=Inject(DB_URL))` does **not** inject; the parameter
    keeps its default. The marker belongs in the annotation, not the default slot.

## `Depends` — a spelling alias

`Depends[X]` is `X` itself, subscripted, so a dependency can be spelled in the type
position lauren core emits. It resolves exactly like the bare type and adds nothing
at runtime.

<!-- docs-contract: run -->
```python
from lauren_di import Depends

class Repo:
    pass

assert Depends[Repo] is Repo
```

It cannot name a `Token` or a string — `Inject` inside `Annotated` is the spelling
for those — and it does not belong in annotation position, where the container
reads the type itself or `Annotated[...]`.

## The four recipes

Each helper returns a frozen `CustomProvider`; `register_custom` lowers it to a row.

| helper | payload | resolves to |
| --- | --- | --- |
| `use_value` | `value=` | the object itself |
| `use_class` | `use=`, `scope=` | an instance built by the container |
| `use_factory` | `factory=`, `injects=()` | whatever the factory returns |
| `use_existing` | `existing=` | what the other token resolves to |

`injects` is the positional argument list handed to the factory, resolved left to
right: a factory has no signature to annotate, so its dependencies are named there.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, Scope, Token, use_class, use_value

SETTINGS = Token("settings")
GREETER = Token("greeter")

class Greeter:
    def greet(self) -> str:
        return "hi"

container = DIContainer()
container.register_custom(use_value(provide=SETTINGS, value={"retries": 3}))
container.register_custom(use_class(provide=GREETER, use=Greeter, scope=Scope.TRANSIENT))
container.compile()

assert await container.resolve(SETTINGS) == {"retries": 3}
assert (await container.resolve(GREETER)).greet() == "hi"
assert (await container.resolve(GREETER)) is not (await container.resolve(GREETER))
```

### Generator factories are teardown hooks

A factory that is a generator — `async` or not — is a resource factory: the first
`yield`ed value is the instance and the rest of the body runs at
`await container.shutdown()`. Extra `yield`s are ignored.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, Token, use_factory

CONNECTION = Token("connection")
events: list[str] = []

def connect():
    events.append("open")
    yield "conn"
    events.append("close")

container = DIContainer()
container.register_custom(use_factory(provide=CONNECTION, factory=connect))
container.compile()

assert await container.resolve(CONNECTION) == "conn"
assert events == ["open"]

await container.shutdown()
assert events == ["open", "close"]
```

### Aliases forward, they do not copy

`use_existing(provide="cache", existing=Cache)` resolves to whatever `existing`
resolves to, in the target's own scope: alias a singleton and both names share one
instance, alias a `Scope.TRANSIENT` token and each resolution builds a new one; an
alias may also point at another alias.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, Scope, injectable, use_existing

@injectable()
class Cache:
    pass

@injectable(scope=Scope.TRANSIENT)
class Job:
    pass

container = DIContainer()
container.register(Cache)
container.register(Job)
container.register_custom(use_existing(provide="cache", existing=Cache))
container.register_custom(use_existing(provide="cache_alias", existing="cache"))
container.register_custom(use_existing(provide="job", existing=Job))
container.compile()

assert await container.resolve("cache_alias") is await container.resolve(Cache)
assert await container.resolve("job") is not await container.resolve("job")
```

An alias to an unregistered token fails at resolve time with `MissingProviderError`
("Alias x -> nope points at an unknown token"); an alias cycle fails with
`CircularDependencyError` ("Circular dependency: a -> b -> a").

## `CustomProvider`

The record is frozen: `provide` (the token), `kind` (`"value"`, `"class"`,
`"factory"`, `"existing"`), the payload field matching that kind (`value`,
`use_class`, `factory` with `inject`, `existing`), plus `name` and `multi`. Kind and
payload are checked when you build it, so a malformed recipe fails before
registration.

<!-- docs-contract: run -->
```python
from lauren_di import use_value

provider = use_value(provide="db_url", value="postgres://x")

assert (provider.kind, provider.provide) == ("value", "db_url")
```

## Multi-bindings

`multi=True` accumulates rows under one token instead of replacing them: resolving
the token returns the rows as a list in registration order, `list[token]` resolves to
the same list, and sorting is yours to do if a consumer needs a priority.
