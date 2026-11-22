# Overrides and isolation

An override substitutes a binding for **one build** without touching the
production registrations. It is a testing tool, not a runtime switch: nothing is
patched globally, and the next container you build starts clean.

## `ProviderOverride`

One substitution: the token to replace, the recipe kind, and that kind's payload.

| field | meaning |
| --- | --- |
| `token` / `kind` | the key being replaced, and the recipe |
| `value` / `values` | the object, or the rows of a multi-binding |
| `use_class` / `factory` | class to build, or callable to call (`injects` names its arguments) |
| `existing` / `scope` / `optional` | another token to re-point at, the scope of the new binding, and skip-if-unregistered |

It validates on construction: an unknown kind or a missing payload raises
`OverrideError` (`code="override_error"`, `bad_payload` in the message) before any
container sees it.

## `OverridePlan`

`OverridePlan(providers={token: override, ...}, modules={old: new, ...})` groups the
substitutions, and `plan.get(token)` returns one or `None`. A plan is a plain value:
build it in a fixture, hand it to a container, build again — it is not consumed.

## The five kinds

`value` hands over an object you already built, `class` builds a fresh instance in
the container's scope, `factory` calls a callable with the tokens in `injects`,
`existing` re-points the token, and `values` supplies a multi-binding's rows.

```python run
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, use_value

CLOCK = Token("clock")

class Frozen:
    def now(self) -> str:
        return "2024-01-01"

plan = OverridePlan({CLOCK: ProviderOverride(CLOCK, "class", use_class=Frozen)})
container = DIContainer(overrides=plan)
container.register_custom(use_value(provide=CLOCK, value=object()))
container.compile()

assert (await container.resolve(CLOCK)).now() == "2024-01-01"
assert container.applied_overrides() == frozenset({CLOCK})
```

`applied_overrides()` lists the tokens actually substituted. Overrides substitute,
they never add: one pointing at a token the container does not register fails with
`OverrideError` and `target_missing` in the message.

## Optional overrides

`optional=True` turns that failure into a skip, which is what a shared fixture
wants when it overrides tokens only some tests register. The build succeeds,
`applied_overrides()` omits the token, and resolving it behaves as if nothing was
registered (`MissingProviderError`).

```python fragment
plan = OverridePlan(
    providers={CLOCK: ProviderOverride(CLOCK, "value", value="2024-01-01", optional=True)}
)
```

## Per-build activation with `building_with`

`DIContainer(overrides=plan)` is the explicit form. When something else builds the
container — a framework entrypoint, a factory you do not own — wrap the
construction:

```python run
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, building_with, use_value

CLOCK = Token("clock")

plan = OverridePlan({CLOCK: ProviderOverride(CLOCK, "value", value="frozen")})
with building_with(plan):
    container = DIContainer()
    container.register_custom(use_value(provide=CLOCK, value="real"))
    container.compile()

assert await container.resolve(CLOCK) == "frozen"
```

The plan is read when the container is constructed: a container built before the
block keeps its own bindings even if you `compile()` it inside, so construct inside
— or pass `overrides=` explicitly.

## Isolation across builds

Two containers built from one plan share no instances: singletons and the `class`
and `factory` kinds are per-container. What they share is whatever you handed over
by reference, plus everything the container never owned.

```python run
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, injectable, use_value

SETTINGS = Token("settings")
CONFIG = {"retries": 3}

@injectable()
class Session:
    pass

plan = OverridePlan({SETTINGS: ProviderOverride(SETTINGS, "value", value=CONFIG)})
first = DIContainer(overrides=plan)
second = DIContainer(overrides=plan)
for container in (first, second):
    container.register_custom(use_value(provide=SETTINGS, value={}))
    container.register(Session)
    container.compile()

assert await first.resolve(SETTINGS) is await second.resolve(SETTINGS)  # shared
assert await first.resolve(Session) is not await second.resolve(Session)  # isolated
```

Not isolated, however you build:

- `value`/`values` payloads and the plan itself: one object and one mapping, handed
  to every build that uses the plan.
- Module-level state — caches, the list a fake appends to, `os.environ` — and real
  I/O in code you did not override: an override removes a binding, not its callers.

## Recommended pytest fixture

One plan per test, a build function, and no module-level container:

```python
import pytest

from lauren_di import OverridePlan, ProviderOverride, Token

CLOCK = Token("clock")

@pytest.fixture()
def plan() -> OverridePlan:
    return OverridePlan({CLOCK: ProviderOverride(CLOCK, "value", value="2024-01-01")})
```

Returning the plan, not the container, keeps tests independent: each test builds its
own container.

## Worked example: before and after

Before — the assertion reads state the collaborator keeps at module level, so it
depends on what earlier tests did:

```python run
from lauren_di import DIContainer, injectable

sent: list[str] = []

@injectable()
class Email:
    def send(self, to: str) -> None:
        sent.append(to)

container = DIContainer()
container.register(Email)
container.compile()
(await container.resolve(Email)).send("a@example.com")
assert sent == ["a@example.com"]
```

After — the collaborator is an override, so the test owns the recording and the
real sender is never constructed:

```python run
from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable

class RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, to: str) -> None:
        self.sent.append(to)

@injectable()
class Email:
    def send(self, to: str) -> None:
        raise AssertionError("the real sender must not run")

recorder = RecordingEmail()
plan = OverridePlan({Email: ProviderOverride(Email, "value", value=recorder)})
container = DIContainer(overrides=plan)
container.register(Email)
container.compile()

(await container.resolve(Email)).send("a@example.com")
assert recorder.sent == ["a@example.com"]
```

The production wiring is untouched, the substitution is visible in the test, and a
second build cannot inherit the first build's instances.
