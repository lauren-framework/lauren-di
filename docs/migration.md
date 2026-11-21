# Migration from lauren

`lauren_di` is the DI core of the `lauren` framework, so most of the surface
keeps its name. What changed is the naming prefix on the private markers and the
removal of every HTTP-bound seam.

## Symbol mapping

| `lauren._di` | `lauren_di` | notes |
|---|---|---|
| `DIContainer` | `DIContainer` | same constructor arguments minus framework values |
| `Provider` | `Provider` | identical immutable record |
| `InjectableMeta` | `InjectableMeta` | identical fields |
| `injectable` | `injectable` | same `scope` / `provides` / `multi` arguments |
| `injectable_meta` | `injectable_meta` | unchanged |
| `Scope` | `Scope` | unchanged, including the numeric order |
| `Token` | `Token` | unchanged |
| `Inject` | `Inject` | unchanged (`Annotated` metadata) |
| `Depends` | `Depends` | unchanged spelling alias |
| `OptionalDep` | `OptionalDep` | unchanged (`injects` list only) |
| `CustomProvider` | `CustomProvider` | unchanged |
| `use_value` / `use_class` / `use_factory` / `use_existing` | same | unchanged |
| `OverridePlan` / `ProviderOverride` | same | unchanged |
| `building_with` / `active_override_plan` | same | unchanged |
| `post_construct` / `pre_destruct` | same | unchanged |
| `ResolutionScope` | `ResolutionScope` | unchanged |

## Marker attribute renames

The decorators write string attributes. Every name moved from the
`__lauren_*__` namespace to `__lauren_di_*__`, so a class can be decorated by
both libraries at once without either reading the other's metadata:

| old attribute | new attribute |
|---|---|
| `__lauren_injectable__` | `__lauren_di_injectable__` |
| `__lauren_post_construct__` | `__lauren_di_post_construct__` |
| `__lauren_pre_destruct__` | `__lauren_di_pre_destruct__` |

If you introspect these attributes directly (rather than through
`injectable_meta`), update the strings. Re-decorate with `lauren_di.injectable`;
the old markers are ignored, so a class carrying only framework markers looks
undecorated here.

## HTTP-bound seams, replaced

| framework concept | `lauren_di` replacement |
|---|---|
| `framework_values` (request, response, path params) | `DIContainer(value_tokens=...)` plus scope `values=` — any token you nominate is supplied from the scope at resolve time |
| `ExtractionMarker` awareness (do not inject fields the extractors own) | `DIContainer(field_predicate=...)` — a callable `(name, annotation) -> bool` you control |
| `LifecycleScheduler` (deferring singleton `@post_construct` until the app boots) | container-owned startup: `await container.startup()` fires hooks for every scope; a singleton is built once, so it fires once |
| module-graph compiler | not supported: register rows yourself, or use `owning_module` + `set_visible` for encapsulation only |
| routing / extractors / ASGI | not supported, by design |

### Supplying request-shaped values

```python
from lauren_di import DIContainer, Scope, Token, injectable

REQUEST_ID = Token("request_id")


@injectable(scope=Scope.REQUEST)
class Handler:
    def __init__(self, rid: str) -> None:
        self.rid = rid


container = DIContainer(value_tokens=(REQUEST_ID,), strict_request_scope=True)
container.register(Handler)
```

Pass `values={REQUEST_ID: "abc"}` to `container.scope(...)` (or to `resolve`)
to satisfy the token per request. Declaring it in `value_tokens` tells `compile()`
that the token is not a missing provider.

### Excluding framework-owned fields

```python
from lauren_di import DIContainer

container = DIContainer(field_predicate=lambda name, annotation: name != "request")
```

The predicate is consulted before a class-body annotation is collected. Return
`False` to leave the field alone.

## Deprecation strategy

There are no compatibility aliases. `lauren` keeps its own `_di` package; this
library is a separate distribution (`lauren-di`) with a separate import name
(`lauren_di`). Migration is a mechanical import rewrite plus the two
constructor-argument swaps above.

## Parity testing

If you maintain both, the invariants worth running against **both**
implementations are:

| invariant | fixture | expected |
|---|---|---|
| singleton identity | register once, resolve twice | same object |
| transient freshness | register `TRANSIENT`, resolve twice | different objects |
| request cache | two resolves inside one scope | same object |
| narrowing rule | singleton depends on request | `DIScopeViolationError` |
| hook order | base + subclass hooks | base first on startup, subclass first on teardown |
| teardown containment | one failing `@pre_destruct` | remaining instances still torn down |
| override isolation | two concurrent `building_with` builds | no cross-talk |

Both packages expose the same error class names, so a parametrised test can
import the module under test and assert on `type(exc).__name__` without
branching.
