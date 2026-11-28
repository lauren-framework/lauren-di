# PRD 16 — Extensible Scopes

**Status:** Draft
**Date:** 2026-11-25
**Depends on:** PRD 01, PRD 03, PRD 05, PRD 15
**Owner PRD of:** D-71 … D-78

`Scope` is a closed three-member `IntEnum`. A downstream application whose
lifetimes are `global > app > container > request > job > transaction` cannot
say so: the enum cannot be subclassed, a foreign enum member is accepted
silently and then cached nowhere and torn down never, and the one error path that
should explain the mistake raises `AttributeError` instead. This page specifies a
declaration protocol — `ScopeDef` + `ScopeSet` + `Carrier` — under which `Scope`
becomes the default ladder rather than *the* type, so an application can define
its own levels and the container still validates, caches and tears down
correctly.

---

## 1. Problem Statement

`Scope` is referenced at **36 sites** across `lauren_di/*.py`, in four different
roles that a single three-member enum happens to satisfy today and that a fourth
level breaks in four different ways.

### 1.1 The enum cannot be extended

The obvious spelling — subclass it and add a member — is not available in
Python, because an `IntEnum` that already has members cannot be subclassed:

```text
>>> class AppScope(Scope):
...     APP = 3
TypeError: <enum 'AppScope'> cannot extend <enum 'Scope'>
```

So the only route open to an embedder is a *separate* enum whose ranks line up
with the built-in numbering by coincidence, and nothing in the library checks
that they do.

### 1.2 A foreign member is accepted silently, then cached nowhere

A standalone `enum.IntEnum` member passes every gate the library has. Measured
on `6869291` with `class AppScope(enum.IntEnum): APP = 3` and
`@injectable(scope=AppScope.APP)`:

```text
compile(): accepted (no scope error)
same instance twice in one scope? False | constructs: 2
child vs parent same? False
teardown ran? False | log: ['construct', 'construct', 'construct', 'construct']
provider scope stored: 3
```

Four constructs, **zero teardowns**. `_container.py:2023` and `:2114` dispatch on
identity — `provider.scope == Scope.SINGLETON`, `== Scope.REQUEST`, else nothing —
so a rank that is neither falls through to the uncached branch, the instance is
never stored, and `finalize_scope` never sees it. The library silently hands out
a leaking transient for a provider that asked for a lifetime.

### 1.3 …and the validation rule then reads the rank as "wider than everything"

`compile()` accepts `AppScope.APP` as a scope value and compares it numerically
(`_container.py:1513`, `for_scope > dependency.scope`). A rank of `3` is
therefore "wider than `SINGLETON`", so a singleton that depends on it is
accepted — the container blesses an edge onto an instance nothing owns.

### 1.4 The error path that should explain any of this raises `AttributeError`

`Scope.label` (`types.py:48`) is a hand-written three-key table, and
`_container.py` renders every scope in a message through it (`:609`, `:1519`,
`:1520`, `:1526`, `:1527`). A foreign member has no `label`:

```text
>>> c.compile()   # @injectable(scope=AppScope.JOB) depending on Scope.REQUEST
builtins.AttributeError: 'AppScope' object has no attribute 'label'
```

The narrowing violation is real, and the diagnostic is a `builtins.AttributeError`
rather than the `DIScopeViolationError` PRD 05 §5.6 promises. The same table
drives `__str__`, so `str()` on a custom scope is `Enum`'s version-dependent
rendering — the exact drifting behaviour PRD 03 §5.1 removed for the built-ins.

### 1.5 The lattice is ints, not levels

`Scope`'s numeric order is load-bearing and undocumented as a *protocol*
(PRD 03 §5.2): a consumer may not outlive its dependency, expressed as one
comparison. That is the correct shape for an extensible ladder, but nothing
states which properties a foreign rank must satisfy, so neither the compiler nor
a reviewer can tell a valid ladder from a coincidence.

### 1.6 The runtime has exactly three carriers, and no name for the missing ones

Whatever the declaration says, `_instantiate` knows three outcomes: the
container's singleton dict, one open scope's request cache, or nothing. PRD 15
§6.3 (D-69) refused to add a fourth *built-in* tier, correctly — but the effect
is that a downstream `app`, `job` or `transaction` level has no carrier at all.
PRD 15 §6.7 records the cost of that gap in the nesting half: a `REQUEST`
instance first touched at a grandchild is torn down when that child closes, while
the request that owns it is still running:

```text
root(1) -> job(2) -> inner(3); a REQUEST object first touched at inner:
after inner closed:  state == 'destroyed'
root then resolves:  s2 is not s   -> 2 sessions created in one request
```

### 1.7 Hosts open a scope the library cannot name

PRD 09 §5.2 makes the host adapter own the scope lifetime, and every adapter
opens the same anonymous scope: `container.scope()`. A host that wants "open the
request level now, the job level here" has one integer — `max_scope_depth`
(PRD 15 §5.6) — and no vocabulary. Nothing checks that the level a host opened is
narrower than its parent, so a second `request` level under a `request` is
indistinguishable from a legitimate one.

---

## 2. Goals

| # | Goal | Testable as |
|---|------|-------------|
| G1 | A scope is declared by a value object — `ScopeDef(name, rank, carrier)` — and a ladder is an ordered, validated set of them (`ScopeSet`) | `T16-01`, `T16-02` |
| G2 | `Scope` remains the default ladder and its members become `ScopeDef` instances, so `@injectable(scope=Scope.REQUEST)` is unchanged and `TRANSIENT = 0` / `REQUEST = 1` / `SINGLETON = 2` stay true | `T16-03`, `T16-04` |
| G3 | A downstream application declares its own ladder and uses it in `@injectable`, `register`, `register_custom`, `Provider.scope` and override plans | `T16-05` … `T16-08` |
| G4 | The runtime dispatches on the declared **carrier**, not on membership in `Scope`, so a custom level caches and tears down at the level it declares | `T16-09`, `T16-10` |
| G5 | A scope that belongs to no ladder the container knows is rejected at registration with a `LifecycleConfigError`, never degraded to an uncached transient | `T16-11`, `T16-12` |
| G6 | A scope-tree node names its level (`scope(level="job")`), the ladder's order is checked when the node opens, and `max_scope_depth` is demoted to a backstop | `T16-13` … `T16-16` |
| G7 | Every message and `detail` payload renders a custom scope through the protocol, so no scope value can raise `AttributeError` from an error path | `T16-17`, `T16-18` |
| G8 | The two readings of "which level caches a new instance" are both specified, with evidence, and recorded as an open item for review | `T16-19`, `T16-20` |

---

## 3. Non-Goals

1. **No new built-in tier.** `Scope` keeps exactly three members. D-69's refusal
   of a fourth built-in `SCOPED` tier stands untouched; this page makes the
   existing set *extensible*, it does not extend it.
2. **No per-provider `level=` keyword.** D-69 also refused that, and for a reason
   this page preserves: `compile()` reads declarations only (PRD 05 §5.4), and a
   lifetime that depends on the call site cannot be validated. A provider names a
   ladder member; the *tree* names levels at open.
3. **No ambient current-scope stack.** Refused by D-58 (PRD 09 §5.3). Resolution
   still requires an object the caller holds.
4. **No dynamic ladder mutation.** A ladder is frozen at container construction;
   `ScopeSet` is immutable and `compile()` records the ladder it validated
   against.
5. **No cross-container ladder inheritance.** A container built with
   `scopes=AppScopes` does not make that ladder the process default; a second
   container declares its own.
6. **No change to teardown order, error containment, or the `DestructError`
   surface** (PRD 03 §5.7, §5.8).
7. **No async scope factories.** A carrier is a cache; entering a scope does not
   construct anything.
8. **No renaming of `strict_request_scope`.** It keeps naming `Scope.REQUEST`;
   §5.9 covers the spelling for a ladder that has no member by that name.

---

## 4. Prior Art

Every mature DI container hit the closed-enum wall, and the ones that solved it
did so by making the *scope a registered object*, not an enum member:

| System | Default scopes | Extension point | Consequence for this design |
|---|---|---|---|
| Spring Framework | `singleton`, `prototype` | `Scope` interface + `ConfigurableBeanFactory.registerScope(name, scope)` | The reference model: a scope is a named object with a cache, registered per container |
| .NET `Microsoft.Extensions.DependencyInjection` | `Singleton`, `Scoped`, `Transient` (`ServiceLifetime` enum) | none — the enum is closed, a custom lifetime means a custom container | The failure mode this page exists to avoid: a closed enum forces downstream to fork the container |
| Google Guice | `Scopes.SINGLETON`, `Scopes.NO_SCOPE` | `@ScopeAnnotation` + `bindScope(annotation, impl)` | Declaration by marker, behaviour by binding; the two halves are separate — mirrored here by `ScopeDef` + `Carrier` |
| Jakarta CDI | `@ApplicationScoped` … | `@NormalScope` + a `Context` implementation | A custom level supplies its own context, i.e. its own cache — the `Carrier.NODE` case |
| `dependency_injector` (Python) | `singleton`, `factory`, `resource` | `providers.Scope` declared with `scope=` and opened as a context manager, arbitrarily nested | Closest prior art for the *tree* half: nesting is an explicit context, levels are declared per provider |
| `injector` (Python) | `singleton`, `threadlocal`, `noprovider` | `@inject` + `scope=` decorator on a class | Shows that a per-provider decorator without an order cannot express narrowing |
| `lauren` framework | `Scope.TRANSIENT`/`REQUEST`/`SINGLETON` (the reference) | none | The parity baseline: PRD 08 §5.x records it, and PRD 15 §1.6 already found nesting absent there |

Two conclusions carry into §5: the **ladder is per container** (Spring, Guice),
and a level is a **cache plus a rule for which node owns it** (Spring's
`Scope.get`, CDI's `Context`). Neither is expressible with the current
`IntEnum` alone.

---

## 5. Proposed API

### 5.1 The three objects

```python fragment
from __future__ import annotations

import enum
from dataclasses import dataclass


class Carrier(enum.Enum):
    """Where an instance of this scope is stored."""

    CONTAINER = "container"  # one per DIContainer, torn down by shutdown()
    NODE = "node"            # one per open scope node of this level
    NONE = "none"            # never cached, never torn down


@dataclass(frozen=True, slots=True)
class ScopeDef:
    """One declared lifetime: a name, a rank, and the carrier that owns it."""

    name: str
    rank: int
    carrier: Carrier

    @property
    def tracked(self) -> bool:
        """True when an instance of this scope is cached, so teardown can find it."""
        return self.carrier is not Carrier.NONE

    def __str__(self) -> str:
        """The name, never enum formatting: messages must be version-stable."""
        return self.name


class ScopeSet:
    """An ordered, validated ladder of ScopeDefs, narrowest first."""

    def __init__(self, *defs: ScopeDef) -> None:
        ...
```

`ScopeDef` is a value object, so it is hashable, comparable, printable and
serialisable without an `Enum` metaclass in the way. `rank` is an `int` and the
ladder is **contiguous from 0**: a gap means a rank that no level owns, which is
exactly the hole §1.2 sends instances into.

### 5.2 `Scope` becomes the default ladder

The built-ins are re-expressed in terms of the protocol, and the public member
names are unchanged:

| Today | After | Carrier | Back-compatible because |
|---|---|---|---|
| `Scope.TRANSIENT` (0) | `ScopeDef("transient", 0, Carrier.NONE)` | none | same value, same name, `label == "transient"` |
| `Scope.REQUEST` (1) | `ScopeDef("request", 1, Carrier.NODE)` | scope node | same value, same name |
| `Scope.SINGLETON` (2) | `ScopeDef("singleton", 2, Carrier.CONTAINER)` | container | same value, same name |

`Scope` keeps its member names, its three ranks and its ordering, but a member
now *is* a documented level rather than a bare integer:

```python fragment
import enum

from lauren_di import Carrier, ScopeDef


class Scope(enum.Enum):
    """The default ladder: three levels, the ranks and names unchanged."""

    TRANSIENT = ScopeDef("transient", 0, Carrier.NONE)
    REQUEST = ScopeDef("request", 1, Carrier.NODE)
    SINGLETON = ScopeDef("singleton", 2, Carrier.CONTAINER)

    @property
    def level(self) -> ScopeDef:
        return self.value

    def __int__(self) -> int:
        return self.value.rank

    def __lt__(self, other: object) -> bool:
        return self.value.rank < _rank(other)

    def __str__(self) -> str:
        return self.value.name


DEFAULT_LADDER = Scope  # prd 16 §5.2: the enum is the default `ScopeSet`
```

What that preserves, exactly:

| Claim | Preserved because |
|---|---|
| `@injectable(scope=Scope.REQUEST)` unchanged at every call site | the member object is still `Scope.REQUEST`; `Provider.scope` accepts it |
| `TRANSIENT = 0`, `REQUEST = 1`, `SINGLETON = 2` | the three **ranks** are unchanged, and `int(Scope.REQUEST) == 1` |
| `Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON` | `__lt__` compares ranks, and `_rank()` accepts a member, a `ScopeDef` or an `int` |
| `label`, `str()` | `str()` returns the level's name, so it stays version-stable (PRD 03 §5.1); `label` is a deprecated alias of `name` |
| `Scope` is the default ladder | `ScopeSet`'s protocol is `__iter__` / `__getitem__` / `__contains__`, all of which an enum supplies once it is wrapped this way, so `DIContainer()` and `DIContainer(scopes=Scope)` are the same container |

Two consequences are deliberate and stated so they are not discovered in review.
First, `Scope` is no longer an `IntEnum`, so `int()` works but arithmetic
(`Scope.REQUEST + 1`) does not — nothing in the library adds scopes, and PRD 03
§5.2's rule is a comparison, not arithmetic. Second, an equality test against a
bare `1` stops being true; §5.9 lists that under deprecations with the
`int(scope)` migration, and the library itself compares ranks, never raw ints.

`@injectable(scope=Scope.REQUEST)` therefore compiles to exactly today's
behaviour, and `carrier` is read from the level rather than inferred from
membership in the enum.

### 5.3 A downstream ladder

```python fragment
from lauren_di import Carrier, DIContainer, ScopeDef, ScopeSet, injectable

AppScopes = ScopeSet(
    Scope.TRANSIENT,                                  # rank 0, uncached
    ScopeDef("app", 1, Carrier.CONTAINER),            # one per container
    ScopeDef("request", 2, Carrier.NODE),             # one per request node
    ScopeDef("job", 3, Carrier.NODE),                 # one per job node
    ScopeDef("transaction", 4, Carrier.NODE),         # one per transaction node
)

container = DIContainer(scopes=AppScopes)


@injectable(scope=AppScopes["job"])
class UnitOfWork:
    ...
```

Notes that the specification fixes:

* `ScopeSet.__getitem__` accepts the name (above) or the `ScopeDef` itself, and
  raises `KeyError` with the known names in the message;
* a ladder may include members of the default ladder (`Scope.TRANSIENT` above),
  which is the migration path for an app that only needs *more* levels; a
  `Scope` member is coerced to its `.value`, and a `ScopeSet` may be passed
  where defs are expected to extend it (`ScopeSet(Scope, ScopeDef("job", 3, Carrier.NODE))`);
* two ladders in one container is a construction error (`invalid_ladder`), so
  there is exactly one ordering the compiler can trust;
* `DIContainer(scopes=None)` is the default ladder, i.e. today.

### 5.4 Providers and the ladder

`Provider.scope` holds a `ScopeDef`. Everything that today stamps, compares or
carries a scope keeps its shape:

| Surface | Today | After |
|---|---|---|
| `@injectable(scope=)` | `Scope` | any member of the container's ladder |
| `register(..., scope=)` | `Scope` | any member of the container's ladder |
| `register_custom(..., scope=)` | `Scope`, not `TRANSIENT` | any ladder member whose `tracked` is `False` is refused, not just `TRANSIENT` |
| `ProviderOverride(scope=)` | may widen only (D-44) | rank comparison within one ladder, unchanged rule |
| `strict_request_scope=True` | `Scope.REQUEST` | the ladder's `request` member, or the narrowest `Carrier.NODE` level if it has none (§5.9) |
| `value_tokens=` seeding | `row.scope is Scope.SINGLETON` | `row.scope.carrier is Carrier.CONTAINER` |

A scope value that is **not a member of this container's ladder** raises
`LifecycleConfigError(reason="unknown_scope")` at registration time — which is
the fix for §1.2 and the only behaviour change an existing user can observe.
The candidate accept list is: a `ScopeDef` in the ladder, a `Scope` member of
the default ladder when that ladder is in use, or a value whose `(name, rank)`
equals a ladder member (so serialised configuration round-trips).

### 5.5 Carriers and runtime dispatch

`_instantiate` (`_container.py:2023` lookup, `:2114` store) is re-expressed as
three carrier cases, and they are the *same* three behaviours the library has
today:

| Carrier | Read from | Written to | Torn down by |
|---|---|---|---|
| `CONTAINER` | the container's singleton map | same | `container.shutdown()` (PRD 03 §5.6) |
| `NODE` | the open scope's cache, read-through to ancestors (PRD 15 §5.3) | §5.7 | the node's `close()` (PRD 15 §5.4) |
| `NONE` | nothing | nothing | never; a generator provider is refused at registration (PRD 03 §5.7) |

Identity checks disappear: `is Scope.SINGLETON` at `_container.py:654` and
`:1366` becomes `carrier is Carrier.CONTAINER`, and a custom `CONTAINER` level
therefore behaves like a second singleton tier *per ladder*, which is what
`app` means in §5.3.

### 5.6 The scope tree names its levels

```python fragment
async with container.scope(level="app") as app:
    async with app.scope(level="request") as request:
        async with request.scope(level="job", values={"job_id": 7}) as job:
            uow = await job.resolve(UnitOfWork)      # cached at the job node
```

| Rule | Statement |
|---|---|
| Name | `level=` accepts a ladder name or `ScopeDef`; omitted means "the narrowest `Carrier.NODE` level", which is `request` for the default ladder |
| Order | a child's rank must be **greater** than its parent's, else `LifecycleConfigError(reason="level_order")` at open, before anything is constructed |
| Introspection | `ResolutionScope.level` returns the node's `ScopeDef`; `depth` (PRD 15 §5.2) stays the raw count |
| Backstop | `max_scope_depth` (D-67) is unchanged and now secondary: it catches a *valid* order repeated too deep, which the ladder cannot |
| Roots | `container.scope(level=...)` names the level a host opens; PRD 15 §5.9's sibling spelling is unaffected |

A ladder therefore lets a host assert its own structure: a second `request`
below a `request` fails at open rather than needing a guessed integer.

### 5.7 Which level caches a new instance — the open question

A `Carrier.NODE` instance has to be stored in *some* node's cache. Two readings
are defensible and this PRD records both. **This decision is proposed, not
settled; it is listed in the register as open for review.**

**Reading A — cache at the level that asked** (today: PRD 15 §5.3, D-65).

```
root(1) -> job(2) -> inner(3); a REQUEST object first touched at inner:
after inner closed:  state == 'destroyed'
root then resolves:  s2 is not s   -> 2 sessions created in one request
parent-first: same object? True
child-first:  same object? False
```

It is simple, it needs no tier bookkeeping, and it gives "a sub-unit gets its own
instance" for free. Its cost is that **lifetime depends on resolution order**: an
object declared `request`-scoped can die with a sub-scope while the request that
declared it is still running, and the request then builds a second one. For a
ladder whose whole purpose is `app > request > job > transaction`, that is the
wrong owner.

**Reading B — cache at the node whose level the provider declares.**

```
same sequence: inner asks for a "request"-tier object
  -> the nearest ancestor-or-self whose level is "request" caches it (root)
  -> inner and root resolve the identical instance; close(inner) tears down nothing
```

Lifetime follows the declaration, search order stops mattering, and a `job`
provider gets its own instance because its tier is `job`. The costs are real:
the write target is no longer `request_cache` (the scope must hand
`_instantiate` a write target), a token first requested below its tier searches
upward, and "give this sub-unit its own copy" becomes impossible without a
second declared level.

The recommendation, for the reviewer to accept or reject: **Reading B for a
declared ladder, Reading A for the two-member default ladder**, since the
default league has no tier between a root and its children for the two readings
to disagree about. Users who need a per-sub-unit instance declare a level for it
(`job`, `transaction`) and get Reading B there. §10 pins the tests that make
either answer checkable, so switching later is a test diff, not a redesign.

### 5.8 Validation and errors

| Check | When | Error | `detail` |
|---|---|---|---|
| ladder contiguity, unique names, exactly one `CONTAINER` carrier at most, rank 0 uncached | `ScopeSet(...)` / `DIContainer(scopes=...)` | `LifecycleConfigError` | `reason: "invalid_ladder"`, `ranks`, `names` |
| scope not in this container's ladder | registration | `LifecycleConfigError` | `reason: "unknown_scope"`, `scope`, `known` |
| consumer outlives dependency | `compile()` (PRD 05 §5.2 phase 3) | `DIScopeViolationError` | `dependent_scope`, `dependency_scope` (names, not `Scope.label`) |
| override widens the target | registration | `LifecycleConfigError` | `reason: "scope_widen"` |
| child level not narrower than parent | `scope(level=...)` | `LifecycleConfigError` | `reason: "level_order"`, `parent_level`, `level` |
| supplied generator for an untracked scope | registration | `LifecycleConfigError` | existing reasons, now `tracked`-based |

Every scope rendered in a message goes through `ScopeDef.name`, so §1.4's
`AttributeError` cannot occur: there is no table to miss a key.

### 5.9 Deprecations and migration

Deprecations are allowed if they buy a cleaner abstraction (the review decision),
and three are worth taking:

| Change | Deprecated | Removed | Migration |
|---|---|---|---|
| `Scope.label` | yes — `ScopeDef.name` is the protocol | next major | `str(scope)` and `scope.name` already agree |
| `max_scope_depth` as *the* way to bound nesting | yes — `level=` + ladder order is the primary | not removed (D-67 rate-limits a real hazard) | keep the cap, add the ladder |
| reading a scope with `is Scope.X` in user code | yes — `carrier` is the predicate | next major | `scope.carrier is Carrier.CONTAINER` |

`strict_request_scope` keeps its name and its meaning. For a ladder with no
`request` member it applies to the narrowest `Carrier.NODE` level, documented in
`docs/guides/scopes.md` rather than left to inference.

### 5.10 A complete program

```python fragment
import asyncio

from lauren_di import Carrier, DIContainer, ScopeDef, ScopeSet, injectable, pre_destruct

AppScopes = ScopeSet(
    ScopeDef("transient", 0, Carrier.NONE),
    ScopeDef("app", 1, Carrier.CONTAINER),
    ScopeDef("request", 2, Carrier.NODE),
    ScopeDef("job", 3, Carrier.NODE),
)


@injectable(scope=AppScopes["app"])
class Settings:
    pass


@injectable(scope=AppScopes["request"])
class Session:
    pass


@injectable(scope=AppScopes["job"])
class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session

    @pre_destruct
    def close(self) -> None:
        ...


async def main() -> None:
    container = DIContainer(scopes=AppScopes)
    container.register(Settings)
    container.register(Session)
    container.register(UnitOfWork)
    async with container.scope(level="request") as request:
        session = await request.resolve(Session)
        async with request.scope(level="job") as job:
            uow = await job.resolve(UnitOfWork)
            assert uow.session is session          # read-through, or Reading B
        assert request.level.name == "request"
        assert request.level.rank < AppScopes["job"].rank   # narrow, not wider


asyncio.run(main())
```

---

## 6. Architecture Fit & Feasibility

### 6.1 Where the change lives

| File | Sites | Change |
|---|---|---|
| `lauren_di/types.py` | `Scope` (all of it) | `Carrier`, `ScopeDef`, `ScopeSet`, `Scope` re-expressed; `label` deprecated |
| `lauren_di/_container.py` | 32 of the 36 | carriers replace identity; ladder validation at registration; level-order at `scope()`; messages render `name` |
| `lauren_di/_custom.py` | 4 (`:238`, `:337`, `:378`, `:410`) | `Provider.scope: ScopeDef`; the `TRANSIENT` refusal becomes `tracked` |
| `lauren_di/_scope.py` | 0 directly | gains `level`, the order check, and (Reading B) a write target |
| `lauren_di/__init__.py` | exports | `Carrier`, `ScopeDef`, `ScopeSet` added to `__all__` |

No new module is needed: the protocol belongs in `types.py`, which PRD 03 §6.5
already designates as the "data only, no container import" layer, and which the
import graph therefore allows `_container` and `_scope` to read.

### 6.2 The 36 sites by role

| Role | Count | Sites | Replacement |
|---|---|---|---|
| default value `scope=Scope.SINGLETON` | 8 | `_container.py:216,670,891,955`, `_custom.py:238,337,378`, `_container.py:807` | `scopes.singleton` (the ladder's own member) |
| identity predicate | 4 | `:654`, `:1366`, `:2023`, `:2114` | `carrier is Carrier.*` |
| ordering comparison | 2 | `:602`, `:1513` | unchanged (`rank`), messages via `name` |
| request-specific runtime | 3 | `:2027`, `:2116`, `:2134` | `carrier is Carrier.NODE` |
| `TRANSIENT` guard | 2 | `:461`, `:985` | `not scope.tracked` |
| placeholder/stamp | 4 | `:442`, `:851`, `:1042`, `:1075` | unchanged, typed `ScopeDef` |
| message rendering (`label`) | 5 | `:609`, `:1519`, `:1520`, `:1526`, `:1527` | `scope.name` |
| import/annotation/`__all__` | 8 | `types.py:17,48,56`, imports | `ScopeDef` alias export |

`grep -c "Scope\." lauren_di/*.py` is 36, and this table accounts for all of
them, so a reviewer can diff the migration mechanically.

### 6.3 Why a value object, not an enum

Three measured facts decide it. First, §1.1: the enum cannot be subclassed, so
"just add a member" is not an option — the extension point must be a *new object
type*. Second, §1.2: an unrelated `IntEnum` is already accepted today and
silently degrades to a leaking transient, so the library cannot distinguish a
foreign scope from a ladder member without a protocol to check membership
against. Third, §1.4: `label` is a closed table keyed by the built-in members, so
every new scope needs a parallel hand-written table somewhere.

`ScopeDef` answers all three: membership is `in ladder`, the carrier is declared
rather than inferred, and `name` cannot be missing. Keeping `Scope` an `IntEnum`
that *is* the default ladder means those answers arrive with no migration for
existing users (G2), which is the only reason the crate can change this much at
rank level without a major version.

### 6.4 Alternatives weighed

| # | Alternative | Why not |
|---|---|---|
| 1 | Subclass `Scope` in the user's code | Impossible: `TypeError: cannot extend <enum 'Scope'>` (§1.1) |
| 2 | Accept any `IntEnum` whose ints happen to line up | Today's silent leak (§1.2): no teardown, no error, no way to check membership |
| 3 | A ladder of plain `str` names, no ranks | Rank is what makes the narrowing rule `compile()` already uses (PRD 03 §5.2) expressible; names alone cannot order an edge |
| 4 | Keep three ranks and let apps map `app`→`SINGLETON` | The mapping is invisible in the declaration and cannot express two `NODE` levels (`job` under `request`) |
| 5 | Register scopes globally (module-level registry) | Two libraries in one process would share an ordering neither controls; Spring's per-factory registry is the proven shape (§4) |
| 6 | Per-provider `level=` keyword | Refused as D-69 and still right: `compile()` reads declarations, and a call-site lifetime is unvalidatable |
| 7 | A `Carrier` that is a callable (user-owned cache factory) | Open-ended: teardown ordering (PRD 03 §5.7) would become user code, and no test could pin it. Three carriers cover the measured needs |
| 8 | Reading B for every ladder, including the default | Changes PRD 15 §5.3 for existing users with no per-sub-unit instance available at all; the recommendation in §5.7 is deliberately narrower |

### 6.5 Evidence index

| Claim | Asserted by |
|---|---|
| the enum cannot be extended | §1.1 transcript, re-run by `T16-01` on the shipped `Scope` |
| a foreign scope compiles and is never cached or torn down | §1.2 transcript, `T16-11`, `T16-12` |
| the narrowing rule reads a foreign rank as "wider than everything" | §1.3 transcript, `T16-12` |
| a foreign scope raises `AttributeError` from the error path | §1.4 transcript, `T16-17` |
| order decides a `REQUEST` instance's lifetime | §1.7 and §5.7 transcripts (`T16-19`, `T16-20`) |
| `Scope` as 0/1/2 is unchanged | `T16-03`, `T16-04`, and `prds/check_consistency.py`'s `symbols` check |
| the 36 sites and their roles | §6.2 table, re-derivable with `grep -c "Scope\." lauren_di/*.py` |
| the default ladder behaves exactly as today | the existing suite: `tests/integration/test_nested_scopes.py` and `tests/unit/test_scope_cache.py` must pass unmodified |

### 6.6 Measurements

Recorded on CPython 3.13.13 against `6869291`, one registration per case, each
number reproduced at least twice:

| Observation | Measured |
|---|---|
| `class AppScope(Scope)` | `TypeError: <enum 'AppScope'> cannot extend <enum 'Scope'>` |
| `compile()` with `scope=AppScope.APP` | accepted, no error |
| two resolves in one scope, `AppScope.APP` | 2 constructs, distinct instances |
| `@pre_destruct` on the same provider | never runs (0 teardowns, 4 constructs) |
| `scope=AppScope.JOB` depending on `Scope.REQUEST` | `builtins.AttributeError: 'AppScope' object has no attribute 'label'` |
| `Scope.` references in `lauren_di/*.py` | 36 |
| a `REQUEST` instance first touched at depth 3 | destroyed when depth 3 closes; the root builds a second |
| coverage gate / current figure | `fail_under = 85.0` / 89.12 % branch |

### 6.7 Risks and limits

1. **A ladder is a container-wide policy, so a second container needs a second
   ladder.** Two libraries in one process cannot share levels, by design (§3.5).
   Mitigation: `ScopeSet` is a value object, so a shared definition can be
   constructed once and passed to both containers; only the *instances* are
   per-container.
2. **`Carrier.CONTAINER` at rank 1 is not `SINGLETON`.** A custom `app` level and
   `singleton` both live in container-owned storage, so a graph that mixes them
   has two container-wide tiers whose teardown is one `shutdown()`. Stated
   because "container carrier" suggests one tier; §5.5's table is the contract.
3. **Reading B costs an upward search on a miss.** A `request`-tier token first
   requested at depth 3 walks three nodes. Measured cost of the equivalent walk
   today is flat to depth 1,000 (PRD 15 §6.6, ~3.3 µs per cache-hit resolve), so
   this is accepted; a future memo must preserve "the declared level owns it".
4. **Deprecating `is Scope.X` in user code cannot be automated.** A user who
   compares identity keeps working for the default ladder (§5.2 keeps the
   objects) but gets no warning until the major. Mitigation: the migration table
   is in §5.9 and `docs/guides/scopes.md`'s "custom levels" section repeats it.
5. **`level=` is a string, so a typo is a runtime error.** Mitigation: accepted
   eagerly at `scope()` with the known names in `detail`, and `ScopeSet.__getitem__`
   is the spelling examples use.
6. **This page does not settle §5.7.** D-77 is registered as open for review on
   purpose: the recommendation (B for declared ladders, A for the default) is a
   judgement about which failure is worse, and it changes what "a sub-unit with
   its own transaction" compiles to.

---

## 7. Error Catalog Impact

| Error | Change | `reason` / `detail` |
|---|---|---|
| `LifecycleConfigError` | **new reasons** | `invalid_ladder` (`ranks`, `names`); `unknown_scope` (`scope`, `known`); `level_order` (`parent_level`, `level`) |
| `LifecycleConfigError` | **widened rule** | the generator-for-untracked-scope refusal is `not scope.tracked`, so a custom uncached level is refused like `TRANSIENT` |
| `DIScopeViolationError` | **message/detail only** | `dependent_scope` and `dependency_scope` are ladder **names**; the `AttributeError` of §1.4 becomes this error |
| `LifecycleViolationError` | unchanged | `depth_exceeded` still comes from `max_scope_depth` (D-67) |
| `DestructError` | unchanged | a custom `NODE` level's failures are reported by the node that created the instance (PRD 15 §5.4) |
| `OverrideError` / `scope_widen` | unchanged rule, ladder-local rank | `hint` renders `name` instead of `label` |

PRD 05 §5.6's catalogue gains three `reason` values and two `detail` fields; no
root-level exception is added or removed.

---

## 8. Phased Implementation Plan

| Phase | Work | Gate |
|---|---|---|
| 1 | `Carrier`, `ScopeDef`, `ScopeSet` in `types.py`; `Scope` re-expressed with `label` kept; `__all__` exports | `T16-01` … `T16-04`; the existing suite unmodified |
| 2 | Carrier dispatch replaces the four identity predicates and the two `TRANSIENT` guards; `Provider.scope: ScopeDef` through `_custom.py` | `T16-09`, `T16-10`; `nox -s typecheck` |
| 3 | Ladder validation at construction and registration (`invalid_ladder`, `unknown_scope`); the §1.2 leak becomes an error | `T16-11`, `T16-12` |
| 4 | `level=` on `container.scope()` and `ResolutionScope.scope()`, `ResolutionScope.level`, level-order check; `max_scope_depth` documented as backstop | `T16-13` … `T16-16` |
| 5 | Message/detail rendering through `name`; deprecation shims and the §5.9 table | `T16-17`, `T16-18`; `nox -s prds` |
| 6 | Whichever §5.7 answer the reviewer picks, with its tests | `T16-19`, `T16-20` |

Each phase is independently revertible: phases 1–2 are behaviour-preserving,
phase 3 is the only one that can reject a graph that compiles today, and phases
4–6 add surface.

---

## 9. Documentation & Convention Updates

1. `docs/guides/scopes.md` gains a **"Custom levels"** section: the `ScopeSet`
   example from §5.3, the level tree from §5.6, and the §5.9 migration table.
2. `docs/reference.md` documents `Carrier`, `ScopeDef`, `ScopeSet` and the
   `scope(level=...)` / `ResolutionScope.level` members.
3. `docs/api/` (mkdocs) gains the three new symbols; the nav is unchanged
   because they live on existing pages.
4. `README.md`'s feature list names extensible scopes without promising a fourth
   built-in tier.
5. `llms-full.txt` / `llms.txt` regenerate through the existing `llms_check`
   corpus gate, which already covers `docs/**` and the CHANGELOG.
6. `CHANGELOG.md` gets an **Added** entry for the protocol, a **Changed** entry
   for `Provider.scope`'s type and the `unknown_scope` rejection, and a
   **Deprecated** entry for `Scope.label` — all under `[Unreleased]`.
7. PRD 03 §5.1 and §5.2 cite this page for the protocol; PRD 05 §5.6 and §7 cite
   the three new `reason` values; PRD 15 §5.3 records which §5.7 answer the
   reviewer chose once D-77 closes; PRD 09 §5.2 names `level=` as how a host
   declares the level it opens.
8. The decision register in `prds/README.md` §4 gains D-71 … D-78 with this page
   in the PRD column, and the index in §2 gains the row for 16.

---

## 10. Test Plan

### 10.1 Enumerated test cases

| ID | Case | Asserts |
|---|---|---|
| T16-01 | ladder construction | a contiguous set builds; a gap, a duplicate name or two `CONTAINER` ranks raise `invalid_ladder` |
| T16-02 | ladder lookup | `ScopeSet["job"]` and `ScopeSet[def]` return the same object; an unknown name raises `KeyError` naming the known levels |
| T16-03 | `Scope` is the default ladder | members are `ScopeDef`s, `TRANSIENT < REQUEST < SINGLETON`, and `TRANSIENT = 0` / `REQUEST = 1` / `SINGLETON = 2` |
| T16-04 | back-compatibility | the existing suite passes unmodified, including `tests/integration/test_nested_scopes.py` |
| T16-05 | a custom member in `@injectable` | a `job` provider resolves and is cached at the job node |
| T16-06 | a custom member in `register(scope=)` | the same, via the imperative spelling |
| T16-07 | a custom member in `register_custom` | a factory provider with a custom `NODE` scope is cached; one with an untracked level is refused |
| T16-08 | a custom member in an override plan | narrowing within the ladder is accepted, widening raises `scope_widen` |
| T16-09 | carrier dispatch — `CONTAINER` | two open scopes hand out the same instance; `shutdown()` tears it down once |
| T16-10 | carrier dispatch — `NODE` | a level's instance is cached per node and torn down by that node's `close()` |
| T16-11 | `unknown_scope` | a foreign `IntEnum` member raises `LifecycleConfigError`, never a transient; `detail["known"]` lists the ladder |
| T16-12 | no silent scope leak | the §1.2 program raises instead of constructing four untorn instances |
| T16-13 | named levels | `scope(level="job")` stores `ResolutionScope.level`, and `level` is a ladder member |
| T16-14 | order check | a child whose rank is not greater than its parent's raises `level_order` before anything is constructed |
| T16-15 | default level | `scope()` with no `level=` on the default ladder is the `request` level, as today |
| T16-16 | `max_scope_depth` backstop | the cap still fires for a valid order repeated too deep, and still raises `depth_exceeded` |
| T16-17 | no `AttributeError` from an error path | the §1.4 graph raises `DIScopeViolationError` with ladder names in `detail` |
| T16-18 | rendering is version-stable | `str(scope)`, `detail` values and log lines use `name` on every supported interpreter |
| T16-19 | Reading A (today) | characterised: an instance first touched below its tier dies with that node and is rebuilt above it |
| T16-20 | Reading B (proposed) | the same program: one instance, owned by the declared tier, torn down when that tier closes |
| T16-21 | deprecation paths | `Scope.label` still returns the name; `carrier` predicates answer for every ladder member |
| T16-22 | `strict_request_scope` on a custom ladder | with no `request` member it applies to the narrowest `NODE` level, and its error names that level |
| T16-23 | two containers, two ladders | each container validates against its own ladder; a scope from the other ladder raises `unknown_scope` |
| T16-24 | value-token seeding | seeding is `carrier is Carrier.CONTAINER`, so a custom container level is seeded like `SINGLETON` |

### 10.2 Gates

- `nox -s tests` on 3.10 … 3.14 with the existing suites **unmodified**, which is
  what makes G2 a gate rather than a claim.
- `nox -s coverage` must stay at or above `fail_under = 85.0` (measured 89.12 %
  on `6869291`); the new protocol needs its own unit tests, not incidental cover.
- `nox -s typecheck` — `ScopeDef`/`ScopeSet` are typed surface, and PRD 01's
  exported-symbol table gains three rows that `tests/docs/test_prd_03_examples.py`
  and the `symbols` check in `prds/check_consistency.py` both read.
- `nox -s prds` — this page's ten sections, links, cross-references and D-71 …
  D-78 register rows.
- `nox -s llms_check` after the corpus regeneration in §9.

### 10.3 Acceptance traceability

| Goal | Cases |
|---|---|
| G1 | `T16-01`, `T16-02` |
| G2 | `T16-03`, `T16-04` |
| G3 | `T16-05`, `T16-06`, `T16-07`, `T16-08` |
| G4 | `T16-09`, `T16-10` |
| G5 | `T16-11`, `T16-12`, `T16-23` |
| G6 | `T16-13`, `T16-14`, `T16-15`, `T16-16` |
| G7 | `T16-17`, `T16-18`, `T16-21` |
| G8 | `T16-19`, `T16-20` |
