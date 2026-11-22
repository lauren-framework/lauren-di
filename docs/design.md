# Design

`lauren_di` is the dependency-injection container extracted from the `lauren`
framework and published on its own. This page is the reasoning: which rules are
load-bearing, which features are deliberately absent, and where the library
knowingly behaves differently from the framework it came from.

The specification behind these notes lives in `prds/`, which is not part of the
published site. Where a rule below carries a decision id (`D-nn`) or an
invariant id (`P-nn`), that is the identifier to quote in an issue. For the
"does this belong here at all" question, see [Contributing](https://github.com/lauren-framework/lauren-di/blob/main/CONTRIBUTING.md).

## The problem

A framework hides its container. `lauren` resolves a handler's dependencies
from the annotations on the handler, so wiring errors surface as a failed
request in a running process, and the container itself is something a user
rarely constructs.

Lift that code out of the framework and the hidden costs become explicit:

- **Ambient state.** The framework keeps a module-level value store and a
  lifecycle scheduler beside the graph. Two tests in one process share them.
- **Import-order sensitivity.** A provider is "registered" by importing the
  module that declares it, so a forgotten import is a `MissingProviderError` at
  request time rather than at boot.
- **Scope leakage.** A long-lived object holding a request-scoped one keeps the
  request alive; the leak appears later as a stale user or a growing heap.
- **Unchecked assumptions.** Nothing verifies that a factory returns what its
  annotation promises, that every declared dependency has a provider, or that
  the graph is acyclic, until the first call that happens to need it.

A hand-rolled container fails in the same four ways, because the first version
is always a dictionary of token to factory plus a recursive `resolve`. It works
until the graph has a cycle, or a provider is registered after the first
resolve, or a test needs the singleton to be a fake.

This container is small on purpose, and the smallness comes from taking five
positions seriously.

## The five load-bearing rules

### Explicit registration

Nothing is discovered by importing a module or walking a package. A class is a
provider because `@injectable` says so, and everything else is registered
through a named container method: `register_class`, `register_factory`,
`register_value`, `register_instance`, `register_alias`, `register_custom`.
Registering the same token twice is a `DuplicateBindingError`, not a silent
overwrite.

```python
from lauren_di import Scope, injectable


@injectable(scope=Scope.SINGLETON)
class Clock:
    """One instance per container."""


@injectable
class ReportService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
```

### Compile-time validation, lazy compile

Validation happens once, over the whole graph, in `compile()`. After that,
resolution is dictionary lookups and no annotation is read again. The compiled
plan is built lazily and invalidated by a late registration, so you never have
to call `compile()` yourself; `startup()` calls it for you. What is checked is
listed in [the validation model](#the-validation-model).

### Scopes are ordered, and the order is the rule

`Scope` is an `IntEnum` — `TRANSIENT = 0`, `REQUEST = 1`, `SINGLETON = 2` — and
a provider may depend only on providers whose scope is at least as long-lived
as its own. The check is an integer comparison, not a special case per pair.

```python
from lauren_di import Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    ...


# Rejected at compile time: a singleton would outlive the request that owns
# its Session. Use Scope.REQUEST here, or move the dependency out.
@injectable(scope=Scope.SINGLETON)
class SessionCache:
    def __init__(self, session: Session) -> None:
        self._session = session
```

### Errors are data with stable codes

Every failure is a `DIError` subclass with a `code` string, a `message`, and a
`detail` mapping, and can serialise itself with `to_payload()` or `as_json()`.
Codes are stable, so a caller can branch on `exc.code` instead of matching a
message, and a log line is machine-readable.

```python
from lauren_di import DIError

try:
    ...
except DIError as exc:
    print(exc.code, exc.detail)
```

The full list is on the [errors](errors.md) page.

### Standard library only

The container imports nothing outside the standard library and supports Python
3.10 and newer. A dependency-injection container sits under every other
dependency, so it is the one place where a transitive dependency is least
affordable.

## Why async-first

`resolve` is a coroutine, and so are `startup` and `shutdown`. The cost is real:
every call site is `async`, and synchronous code cannot use the container
without an event loop. Two things buy that back.

There is no sync/async split in the API, so there is no bug class where a
factory returns a coroutine that is then injected as if it were the object. And
teardown is uniform: a provider's destructor may await, because `shutdown()`
returns a tuple of `DestructError` after everything has been awaited in reverse
creation order.

The rule that follows from it: a factory is a coroutine function, or a plain
function returning a value — never a plain function returning a coroutine. The
last one is an error the container can only report by inspecting what it got
back, and it is better not to write it.

## What is deliberately absent

- **No global container.** A `DIContainer` owns its graph; there is no module
  level registry and no ambient value store. Two containers in one process share
  nothing.
- **No import scanning.** Registration is a decorator or a method call. Nothing
  walks your packages.
- **No reflection at resolve time.** Annotations are read once, during compile.
- **No HTTP awareness.** A field annotated with a marker such as
  `Annotated[str, Path]` is not a dependency; see P-6 below. Which annotations
  count as declarations is the host's decision, expressed with
  `field_predicate`.
- **No request lifecycle.** The container does not know what a request is. A
  `ResolutionScope` caches for as long as its owner keeps it; starting and
  ending requests belongs to the framework.
- **No configuration loading, no logging, no metrics.** Providers, values, and
  errors are the whole surface.

The boundary test in [Contributing](https://github.com/lauren-framework/lauren-di/blob/main/CONTRIBUTING.md) turns these into a
decision procedure: if a feature needs to know about HTTP, about a request, or
about the process, it belongs in the framework and not here.

## The validation model

`compile()` walks the graph once and fails on the first problem it can prove.

| Checked | How |
| --- | --- |
| Override targets | Every override names a registered token with a compatible shape, before the graph walk begins |
| Constructor parameters | Each parameter is a dependency unless it has a default; a missing provider is `MissingProviderError` |
| Class-body fields | Every annotated field is a required dependency; the module owning the class must be declared |
| `list[T]` members | A list annotation collects the multi-bindings for `T`; an empty list is valid, an ambiguous one is not |
| Alias edges | Followed to their terminal provider, with cycle detection over the whole graph |
| Scope violations | Each edge is compared in the declaring row's scope; an alias edge is judged by its terminal target |
| Ambiguity | Two visible candidates for one token is `ProtocolAmbiguityError`; invisible candidates are filtered first |
| Value tokens | A token the host supplies out of band is skipped: no provider is expected for it |

What it does not check, on purpose:

- **Runtime types.** No `isinstance` against the annotation. A factory that
  returns the wrong type is a bug in the factory, and the container does not pay
  for a check on every resolution to find it.
- **Factory bodies.** Only the signature and the return annotation are read.
- **The contents of `values=`.** A value token is checked when it is needed,
  which is at resolve time, not at compile.
- **Anything HTTP.** Markers are the host's business (P-6).

## The parity contract and the divergence register

The library must be a drop-in replacement for the framework's `_di` package, so
the test suite runs the same fixtures twice: once against a reference backend
that models the framework's behaviour, once against the library. Anywhere the
two disagree is a failure, unless the disagreement is recorded.

Exactly five are recorded. They are not oversights and they are not a backlog;
each one is an assertion a test enforces.

| Invariant | Reference behaviour | This library |
| --- | --- | --- |
| `values=` (D-52) | Out-of-band values arrive through a module-level value store and a `framework_values=` keyword, so the mapping is ambient | Values are an explicit `values=` mapping passed to `resolve()` or `scope()`; no module-level state |
| P-6 (D-54) | An HTTP marker field is collected as a dependency, so the container raises `MissingProviderError('str')` (`E-404`) | The field is left unset and `str` is never requested; `field_predicate` decides what is a declaration |
| P-11 (D-53) | Neither `@post_construct` hook fires at resolve, and a running lifecycle scheduler is required (`E-420`, `E-423`) | Singleton hooks fire at `startup()`, request-scoped hooks fire at `resolve()` |
| P-13 (D-25) | A class-body field whose annotation names a function provider is collected, and the factory's value is injected (`E-409`, `E-409b`) | The field is left unset; a callable annotation is not a declaration |
| P-14 (D-27) | The alias is never followed, so a singleton consumer of a request-scoped alias target is accepted and the row reports `SINGLETON` (`E-47`, `E-49`) | The alias is followed to its terminal target and `DIScopeViolationError` reports the terminal scope |

Each row is one decision, and each decision has a test. If a sixth divergence
appears in the parity run, either the library or the reference is wrong; the
register does not grow to accommodate it.

## Performance

- **Compile is one traversal.** Cost is proportional to the provider count plus
  the edge count, paid once, and the result is cached on the container. A late
  registration invalidates it, and the next resolve rebuilds it lazily.
- **Resolve is a lookup.** After compile, a token maps to a row in a dictionary;
  no annotations are read, no `isinstance` runs, and no string is parsed.
- **Singletons are cached on the container**, one instance per token per
  container. A second container is a second graph, with its own singletons.
- **The request cache is not the container's.** `container.scope()` returns a
  `ResolutionScope` that owns the cache for request-scoped instances and dies
  with the scope, so a long-lived container accumulates nothing per request.
- **Teardown is ordered, not scanned.** Creation order is recorded as objects
  are built, and `shutdown()` walks it backwards.

## Changing a design decision here

The reasoning above is a summary. The decisions themselves are numbered in the
PRDs, which stay in the repository but out of the published site:

| PRD | Covers |
| --- | --- |
| `prds/01` | Product surface and the public API |
| `prds/03` | Scopes and their ordering |
| `prds/04` | Custom providers and the registration API |
| `prds/08` | Framework compatibility, migration, and the divergence register |

To change observable behaviour, follow the same path as everything else here:

1. Find the decision id (`D-nn`) the change touches, or add one. A decision
   without an id is not a decision, it is an opinion.
2. Decide whether the change is *faithful* or a *divergence*. Faithful means the
   reference backend changes too, and the parity fixtures move with it.
3. If it is a divergence, register it in three places: a row in PRD 08 §5.9 with
   its invariant id, an entry in `DIVERGENCES` in `tests/parity/backends.py`,
   and a note on the [migration](migration.md) page for readers arriving from
   the framework. The count in this page changes only when all three do.
4. Add the test that asserts the new behaviour. A recorded divergence that no
   test enforces is indistinguishable from a bug.

Adding a sixth divergence is allowed. Adding one silently is not: the register
is the only thing that distinguishes "we chose this" from "we got this wrong".
