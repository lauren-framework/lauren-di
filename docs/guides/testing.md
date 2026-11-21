# Overrides and isolation

An override substitutes a binding for **one build** without touching the
production registrations. It is a testing tool, not a runtime switch.

## `ProviderOverride`

```python
from lauren_di import ProviderOverride

override = ProviderOverride(token="clock", kind="value", value=FrozenClock())
```

| field | meaning |
|---|---|
| `token` | which row to replace |
| `kind` | `"value"`, `"values"`, `"class"`, `"factory"`, `"existing"` |
| `value` / `values` | payload for `value` / `values` |
| `use_class` | payload for `class` |
| `factory` + `injects` | payload for `factory` |
| `existing` | payload for `existing` |
| `scope` | override the row's scope (`None` inherits the target's) |
| `optional` | when `True`, a miss is ignored instead of raising |
| `role` | `"provider"` (default) or `"module"` |

Bad payloads — an empty `values` tuple, an `injects` entry that is not a usable
token — raise `OverrideError` with `code="override_error"` at construction
(`reason="bad_payload"`).

## `OverridePlan`

```python
from lauren_di import OverridePlan

plan = OverridePlan({"clock": override})
```

`OverridePlan` also accepts `modules={original: replacement}`, which swaps
module identities for visibility checks.

## The five kinds

```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable


@injectable()
class Clock:
    def now(self) -> str:
        return "real"


class Frozen:
    def now(self) -> str:
        return "frozen"


plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=Frozen())})
container = DIContainer(overrides=plan)
container.register(Clock)
container.compile()
```

| kind | replaces with | example payload |
|---|---|---|
| `value` | one ready object | `value=Frozen()` |
| `values` | a whole multi-binding | `values=(a, b)` |
| `class` | a class the container builds | `use_class=Impl` |
| `factory` | a callable plus its injects | `factory=make, injects=(Dep,)` |
| `existing` | another token in the same container | `existing=Real` |

A `value`/`values`/`existing` override **inherits the target row's scope**, so
overriding a singleton stays a singleton. `class`/`factory` overrides take their
own declared scope.

## Optional overrides

`optional=True` means "apply if the target exists, otherwise say nothing".
It is how a test profile can override things that a minimal build never
registered.

```python
from lauren_di import ProviderOverride

maybe = ProviderOverride(token="metrics", kind="value", value=None, optional=True)
```

Without `optional`, an override whose target never gets registered raises
`OverrideError` with `reason="target_missing"` when you `compile()`.

## Per-build activation with `building_with`

`building_with` sets the active plan in a `ContextVar`. It must wrap
**registration**, because that is when overrides are matched to rows:

```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, building_with, injectable


@injectable()
class Clock:
    def now(self) -> str:
        return "real"


class Frozen:
    def now(self) -> str:
        return "frozen"


plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=Frozen())})

with building_with(plan):
    container = DIContainer()
    container.register(Clock)
```

`active_override_plan()` returns the plan in force, or `None`. Because it is a
`ContextVar`, two concurrent builds never see each other's plans, and nesting
restores the outer plan on exit.

## Recommended pytest fixture

One fresh container per test, no shared state:

```python
import pytest

from lauren_di import DIContainer, OverridePlan


@pytest.fixture
def container() -> DIContainer:
    """A container with no registrations and no overrides."""
    return DIContainer()


def build_container(*, overrides: OverridePlan | None = None) -> DIContainer:
    """A container whose rows were registered under a given plan."""
    return DIContainer(overrides=overrides)
```

Keep registration inside the fixture (or a helper it calls). Registering once
in a module-level container and reusing it across tests is the usual way
overrides leak.
