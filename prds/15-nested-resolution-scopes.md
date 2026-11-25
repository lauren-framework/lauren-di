# PRD 15 — Nested Resolution Scopes

**Status:** Draft
**Date:** 2026-11-24
**Depends on:** PRD 01, PRD 03, PRD 09
**Owner PRD of:** D-64 … D-70

A scope can now open a child scope — `with container.scope() as scoped` giving
`scoped.scope()`, and that child giving another — to any depth, with a
read-through request cache, values inherited by copy, deterministic teardown,
and a depth limit that is unlimited by default and configurable when a graph or
a host needs a bound. This page specifies the semantics, measures the cost, and
records what the alternative readings were rejected for.

---

## 1. Problem Statement

`ResolutionScope` is one flat object with one cache. Nothing in the surface
lets a scope open another scope *from itself*, so the only way to nest today is
to call `container.scope()` again — and that produces a **sibling**, not a
child. The gap is small in code and large in semantics, and every part of it is
measurable on the tree this page was written against.

### 1.1 `scoped.scope()` does not exist

Measured on `lauren_di/_scope.py` at `f4d4a96`:

```
>>> scoped = container.scope()
>>> scoped.scope()
AttributeError: 'ResolutionScope' object has no attribute 'scope'
>>> [n for n in dir(scoped) if not n.startswith("_")]
['close', 'closed', 'errors', 'resolve', 'values']
```

A caller who reads PRD 03 §5.3 ("`container.scope()` returns a `ResolutionScope`")
and then writes `scoped.scope()` gets an `AttributeError` from the one object the
PRD calls the owner of the request cache. There is no documented spelling for
"a scope inside a scope" other than starting a second root.

### 1.2 The sibling reading makes nesting a copy, not a hierarchy

`container.scope()` inside an open scope is a fresh root: an empty cache, no
values, no relationship. Measured:

| Observation | Measured |
|---|---|
| `inner.resolve(Session) is outer_session` | `False` — the child constructs its own instance |
| `inner.values` | `{}` although `outer.values` is `{<class 'str'>: 'v'}` |
| `inner.parent`, `inner.depth` | absent |

`docs/guides/scopes.md` states this as the design ("a nested scope is a sibling
of its parent, not a child"). That reading is coherent but it is *not* nesting:
a unit of work that wraps two sub-units of work — a request that dispatches two
jobs, a CLI command that runs two transactions — cannot say "share my session,
but give each sub-unit its own transaction", which is the only reason to nest at
all. The alternative — pass the parent's cache to
`container.resolve(request_cache=...)` by hand — is the low-level seam PRD 03
§5.3 rule 3 warns performs no teardown.

### 1.3 Closing order is unenforced, so a child can outlive what it reads

Because siblings are unrelated, nothing notices when the parent closes first.
Measured:

```
parent = container.scope(); child = container.scope()
await parent.resolve(Session); await child.resolve(Session)
await parent.close()
parent.closed is True, child.closed is False
await child.resolve(Session)  # still works, after the parent's teardown
```

With read-through (§5.3) the same sequence becomes a correctness bug rather
than a tidiness bug: the child's cache *points at* the parent's, so a parent
that closes first either hands the child a destroyed instance or — because the
parent clears its dict — silently reconstructs one at the child, tearing it down
later at the wrong level.

### 1.4 "Infinite" is only true until the interpreter's stack

`container.scope()` sibling chains reach 500 levels without trouble, so
unbounded nesting looks free. It is not free for the two operations this page
adds. Both were measured on a first cut that walked the chain recursively:

* **close** — one `await` per level: `RecursionError` at ~1,000 levels;
* **cache lookup** — one `__contains__` per level: `RecursionError` at ~1,000
  levels, on the *resolve* path, so even reading the cache of a deep scope
  failed.

A feature whose headline is "infinite" cannot fail at a thousand when the
interpreter's default limit is exactly that. Both walks are loops in the
shipped implementation (§5.7, §6.2), and the 2,000-level case is a test.

### 1.5 Nothing can bound the depth

There is no `max_depth` on the container and none on the scope, so an accidental
recursive open — a decorator that opens a scope per call, a helper that opens a
scope "to be safe" — grows until memory or the stack runs out. A library that
says "one scope per unit of work" (PRD 09, IC-3) should be able to *enforce* the
bound a host declares rather than document it.

### 1.6 The reference implementation has no hierarchy either

`../lauren-framework`'s `lauren/_di/_scope.py` models a single request scope
owned by the ASGI layer; nesting is not expressible there because there is no
object a user can open. So this page is not a parity exercise: there is nothing
to be at parity *with*. That is why every decision below is stated as a
`lauren-di` decision (D-64 … D-70) rather than as a divergence from the
reference.

---

## 2. Goals

| # | Goal | Testable as |
|---|------|-------------|
| G1 | A live scope opens a child scope with `scoped.scope()`, and a child can open its own child, to any depth | `T15-01`, `T15-02` |
| G2 | A child reads through its ancestors' `REQUEST` instances and inherits their `values`; only what it creates is cached and torn down at its own level | `T15-03` … `T15-09` |
| G3 | Teardown order is deterministic and level-correct: a child's instances go before an ancestor's, and closing a parent closes the children nobody closed | `T15-10` … `T15-13` |
| G4 | The depth is configurable — unlimited by default, capped per container and narrowed per subtree — and the check happens before anything is constructed | `T15-16` … `T15-20` |
| G5 | "Infinite" is true in practice: a 2,000-level chain resolves and closes without touching the interpreter's stack | `T15-21` |
| G6 | Nothing that already worked changes: `container.resolve(request_cache=...)` still takes a plain `dict`, a root scope is a root, and the sibling spelling is still available | `T15-22`, `T15-23` |

---

## 3. Non-Goals

1. **No per-level provider lifetime.** A provider cannot declare "level 2". The
   `Scope` enum stays `TRANSIENT = 0`, `REQUEST = 1`, `SINGLETON = 2`
   (PRD 03 §5.1) and `REQUEST` keeps its meaning: *one instance per scope
   level*. The alternative is weighed and rejected in §6.4 row 2 and recorded as
   D-69.
2. **No ambient scope registry.** There is no `current_scope()` `ContextVar` and
   no thread-local "active scope" stack. D-58 already refuses an ambient scope
   for hosts because it does not survive a WSGI thread hop or Django's
   `sync_to_async`; a child scope is an object a caller holds, not a fact the
   library guesses.
3. **No transport, no router, no job model.** The child is opened by the code
   that knows what the nesting means. The library never derives a level from a
   request, a task or a call depth.
4. **No automatic parallelism.** Two children of one parent are sequential
   objects; running them concurrently is the caller's business and the cache is
   not synchronised (PRD 03 §5.3 rule 6 says the same for a plain scope).
5. **No migration surface.** Nothing in `lauren` exposes nested scopes, so there
   is no alias, no shim and no PRD 08 entry.
6. **No change to `DestructError` semantics.** Teardown still returns failures
   and never raises for them (D-20); cascade closing collects per level.

---

## 4. Prior Art

| System | Model | What this page takes, and what it does not |
|---|---|---|
| .NET `IServiceProvider` / `IServiceScope` | `IServiceScopeFactory.CreateScope()` opens a nested scope; `REQUEST`-equivalent (`Scoped`) instances are per scope; `SINGLETON` lives on the root provider | The hierarchy and the read-through direction are the same. .NET needs a *factory* object because a scope has no back-reference; here the scope is the factory (`scoped.scope()`), which is one object fewer. |
| Java CDI | `@RequestScoped` / `@SessionScoped` / `@ConversationScoped` with a `Conversation` that can be nested by id | Confirms that nesting is only useful when a sub-unit can *share* its parent's instances; the conversation *lifecycle* (begin/end/restore) is stateful in a way `lauren-di` has no vocabulary for. |
| Spring Framework | `@Scope(value = "request", proxyMode = ...)`; a child `ApplicationContext` with a parent | The parent-chain lookup is exactly §5.3's read-through. A child context can also override a bean, which here is `values` + overrides at build time (PRD 07), not a new scope feature. |
| NestJS | Request-scoped providers bubble up the module tree ("bubbling") | A cautionary tale: making a *provider's* level change its consumers' level makes the level invisible in the declaration. D-69 keeps the level in the **call**, never in the provider. |
| `dependency-injector` (Python) | `containers.DeclarativeContainer` with `Scoped` providers, single scope stack per container | Shows the failure mode of an *implicit* stack: two concurrent requests on one container share it. This page's child is explicit and per-caller (§3 item 2). |
| `asyncio.TaskGroup` | Structured concurrency: a parent cannot exit before its children | The close-order rule of §5.7 is the same idea without owning the tasks: a parent scope's `close()` closes its children first. |

---

## 5. Proposed API

### 5.1 Public surface

One new method, one new constructor keyword and four new members — nothing is
removed:

| Symbol | Signature | Specified in |
|---|---|---|
| `ResolutionScope.scope` | `scope(*, values: Mapping[Any, Any] \| None = None, max_depth: int \| None = None) -> ResolutionScope` | §5.3, §5.6 |
| `ResolutionScope.parent` | `ResolutionScope \| None` | §5.2 |
| `ResolutionScope.depth` | `int` | §5.2 |
| `ResolutionScope.child_scopes` | `child_scopes() -> tuple[ResolutionScope, ...]` | §5.7 |
| `DIContainer.max_scope_depth` | `DIContainer(*, …, max_scope_depth: int \| None = None)` | §5.6 |
| `DIContainer.scope` | `scope(*, values: Mapping[Any, Any] \| None = None, max_depth: int \| None = None) -> ResolutionScope` | §5.6 |

`Scope` is unchanged. `DIContainer.resolve`'s `request_cache` annotation is
unchanged (`dict[Any, Any] | None`, PRD 01 §5.3.3) — see §6.2 for why that is a
design constraint rather than an accident.

### 5.2 Depth and identity

A scope opened by `container.scope()` is a **root**: `depth == 1`,
`parent is None`. A scope opened by `scoped.scope()` is the parent's depth plus
one, and holds a strong reference to its parent, so a child cannot outlive the
object it reads through.

```python fragment
root = container.scope()
child = root.scope()
assert (root.depth, child.depth) == (1, 2)
assert child.parent is root and root.parent is None
```

`depth` is a property of the object, not of the call stack: two siblings opened
from the same parent both report `parent.depth + 1`. Nothing about depth is
stored globally, so two chains cannot interfere.

### 5.3 Read-through resolution

Resolving through a child consults **its own cache first, then its ancestors',
nearest first**. A hit anywhere returns that instance unchanged and caches
nothing at the child; a miss constructs the instance and caches it at the level
that asked. That single rule produces the whole table:

| Provider scope | Resolved at | Result |
|---|---|---|
| `SINGLETON` | any level | the container's one instance |
| `REQUEST` | the level that created it, or any descendant | the same instance |
| `REQUEST` | an ancestor of the level that created it | a *different* instance, created there (the child's cache is below it) |
| `REQUEST` | a level whose ancestor has none | a new instance, cached **there** |
| `TRANSIENT` | any level | a new instance, never cached |

```python run
import asyncio

from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


@injectable(scope=Scope.REQUEST)
class Repo:
    def __init__(self, session: Session) -> None:
        self.session = session


async def main() -> None:
    container = DIContainer()
    container.register(Session)
    container.register(Repo)

    async with container.scope() as root:
        session = await root.resolve(Session)

        async with root.scope() as child:
            assert await child.resolve(Session) is session  # read-through
            repo = await child.resolve(Repo)                # created at the child
            assert repo.session is session
            assert await child.resolve(Repo) is repo

        # The child created Repo alone; the parent has none yet.
        parent_repo = await root.resolve(Repo)
        assert parent_repo is not repo
        assert parent_repo.session is session


asyncio.run(main())
```

The lookup direction is why `request_cache` could stay a `dict`: the child's
cache is a `dict` subclass whose membership test walks a `_parent` link (§6.2).
A parent never consults a child, so a child's lifetime stays inside its parent's.

### 5.4 What teardown owns

Each level owns exactly the entries in its own dict:

* `close()` finalises `self._cache` only — iterating it yields *this* level's
  instances, never an ancestor's, so an inherited instance is never torn down
  twice or at the wrong level;
* an inherited instance keeps living at the level that created it, until that
  level closes;
* the errors a level returns are its own; a child's failures are readable on the
  child (`child.errors`), because the parent did not create them.

This is a property of the cache object, not a convention: a mapping *chain*
(e.g. `collections.ChainMap`) would report the union of the chain when iterated,
and `finalize_scope` would tear the parent's instances down at the child. The
dict subclass is chosen for exactly that reason (§6.2).

### 5.5 Values

A child's values are a **copy of its parent's, updated with its own**, taken
once when the child is created:

| Rule | Statement |
|---|---|
| Inherited | `child.values` contains every key `parent.values` had at creation |
| Merged | a key passed to `scoped.scope(values=…)` wins over the parent's |
| Snapshot | later mutation of either dict is **not** visible to the other |
| Transitive | a grandchild inherits what its parent has, which already includes the grandparent's |

```python fragment
async with container.scope(values={"tenant": "acme"}) as root:
    async with root.scope(values={"tenant": "beta", "job": 7}) as child:
        assert child.values == {"tenant": "beta", "job": 7}
    assert root.values == {"tenant": "acme"}
```

Snapshot semantics are chosen over a live view because the alternative makes a
child's resolution depend on *when* the parent mutated its mapping, which is not
observable from the child's code. A host that wants live state puts the object
in `values`, not the value: `{"request": request}` still hands the same
`request` everywhere.

### 5.6 Configurable depth ("levels")

The nesting depth is unlimited by default, and configurable at two points:

| Where | Spelling | Effect |
|---|---|---|
| Container | `DIContainer(max_scope_depth=N)` | every root this container opens is capped at `N` levels |
| One root | `container.scope(max_depth=N)` | caps this chain only |
| One subtree | `scoped.scope(max_depth=N)` | caps this subtree; **may only narrow**, never widen |

Checked **before** any child is constructed, so an over-deep call costs a
comparison:

```python run
import asyncio

from lauren_di import DIContainer, LifecycleConfigError, LifecycleViolationError, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass


async def main() -> None:
    container = DIContainer(max_scope_depth=2)
    container.register(Session)

    root = container.scope()          # depth 1
    child = root.scope()              # depth 2

    try:
        child.scope()                 # depth 3 > 2
    except LifecycleViolationError as exc:
        assert exc.detail == {"depth": 3, "max_depth": 2, "reason": "depth_exceeded"}

    try:
        child.scope(max_depth=10)     # widening is a declaration error
    except LifecycleConfigError as exc:
        assert exc.detail["reason"] == "widened_max_depth"


asyncio.run(main())
```

| Condition | Error | `detail` |
|---|---|---|
| `max_depth` / `max_scope_depth` below 1 | `LifecycleConfigError` | `{"max_depth": n, "reason": "invalid_max_depth"}` |
| a child asks for a wider limit than it inherited | `LifecycleConfigError` | `{"max_depth": n, "inherited_max_depth": m, "reason": "widened_max_depth"}` |
| opening level `depth` beyond the effective limit | `LifecycleViolationError` | `{"depth": d, "max_depth": m, "reason": "depth_exceeded"}` |

`max_depth` is spelled on the scope and `max_scope_depth` on the container
because the container's value is a policy for every chain it will ever open,
while the scope's is a bound for one chain; one name for both would hide which
one a call is overriding.

### 5.7 Closing: children first, iteratively

`close()` still tears down the level's own cache newest-first (PRD 03 §5.7) and
still never raises for teardown failures. It gains one rule and one constraint:

1. **Children first.** `close()` walks the subtree, closes every level that
   nobody closed, leaves last. A parent's instance is never torn down while a
   child that reads through it is still resolvable.
2. **Iteratively.** The walk uses an explicit stack and the cache lookup walks
   a `_parent` link in a loop (§6.2), so 2,000 levels behave like 2.

`child_scopes()` reports which children are still open — for diagnostics, and
because it is the one thing a caller can observe before `close()` cascades:

```python fragment
root = container.scope()
left, right = root.scope(), root.scope()
assert [s.depth for s in root.child_scopes()] == [2, 2]
await root.close()
assert left.closed and right.closed and root.child_scopes() == ()
```

A closed scope refuses to open a child (`LifecycleViolationError`,
`detail["state"] == "closed"`), and resolving through a closed child raises with
the same `state`. Both existing messages gain `"depth"` / keep their old keys, so
no caller that parsed them breaks.

### 5.8 Interaction with the rest of the surface

| Feature | Behaviour at a child scope |
|---|---|
| Strict request scope | a child is an active scope: `strict_request_scope=True` resolves `REQUEST` providers through it normally |
| `value_tokens` | unchanged: a value token is satisfied from the level's merged `values` |
| Generator providers | wrapped and cached at the creating level; the wrapper closes there (§5.4) |
| `multi=True` bindings | the cache key is `id(provider)`, so read-through is per *row*, not per token — two siblings stay distinct at every level |
| `@post_construct` / `@pre_destruct` | run at construction and teardown of the level that created the instance, exactly once |
| Aliases | transparent (D-27): the terminal target's scope decides, and read-through is per row |
| `compile()` | untouched: no validation reads a scope, because a level is a runtime fact |
| Overrides | untouched: a plan is applied at build time (D-44) |
| `container.shutdown()` | untouched: singletons are container-wide, not level-wide |

### 5.9 Root nesting stays available

`container.scope()` inside an open scope is still a root — a second chain with an
empty cache and no inherited values. It is the escape hatch for work that must
not share request state (a background job started from a request, a cache
priming pass), and it is what `lauren`'s ASGI layer already does once per
request. The sibling spelling is therefore not deprecated; it is the *other*
meaning, and §5.3's table is the one to read when choosing.

### 5.10 A complete program

```python run
import asyncio

from lauren_di import DIContainer, Scope, injectable, pre_destruct

order: list[str] = []


@injectable(scope=Scope.REQUEST)
class Tx:
    @pre_destruct
    async def rollback(self) -> None:
        order.append("tx")


@injectable(scope=Scope.REQUEST)
class Batch:
    def __init__(self, tx: Tx) -> None:
        self.tx = tx

    @pre_destruct
    async def close(self) -> None:
        order.append("batch")


async def main() -> None:
    container = DIContainer()
    container.register(Tx)
    container.register(Batch)

    async with container.scope(values={"job": "nightly"}) as root:
        tx = await root.resolve(Tx)                     # created at the root
        async with root.scope() as child:
            batch = await child.resolve(Batch)
            assert batch.tx is tx                       # read-through
        assert order == ["batch"]                       # the child owned Batch only
    assert order == ["batch", "tx"]                     # the root closed last


asyncio.run(main())
```

---

## 6. Architecture Fit & Feasibility

### 6.1 Where the change lives

| File | Change | Lines (approx.) |
|---|---|---|
| `lauren_di/_scope.py` | `_ScopeCache`, `ResolutionScope.parent` / `.depth` / `.scope` / `.child_scopes`, iterative `close`, `_live_children` | ~150 |
| `lauren_di/_container.py` | `max_scope_depth` constructor keyword, `scope(max_depth=…)` | ~25 |
| `docs/reference.md` | regenerated from the docstrings (`scripts/build_reference.py`) | generated |
| `tests/integration/test_nested_scopes.py` | the T15 plan | 24 cases |
| `tests/unit/test_scope_cache.py` | the cache contract §5.4/§6.2 rest on | 6 cases |

No new module, no new export, no new dependency: `weakref` is stdlib, so
`dependencies = []` (D-3) is untouched, and `tests/packaging/`'s import-site
census moves by exactly one (`import weakref`).

### 6.2 Why a dict subclass, not a mapping chain

Three published contracts collide with the obvious `ChainMap`:

1. **`request_cache` is typed `dict[Any, Any] | None`** (PRD 01 §5.3.3), and
   `tests/unit/test_resolve_contract.py` compares the PRD row with the source
   annotation. Widening it to `MutableMapping` would be a public API change for
   a private need.
2. **`finalize_scope` iterates the cache it is given** (PRD 03 §5.7). A chain
   iterates the union, so a child would tear down the parent's instances, and
   `container.shutdown()` already relies on the dict being the level's own.
3. **Embedders pass their own dict** (PRD 09 §5.3): whatever the library hands
   `resolve` must accept the same writes as a plain `dict`.

`_ScopeCache(dict)` satisfies all three: it stores only its own entries,
inherits `__setitem__`/`__iter__`/`__len__` from `dict`, and overrides the two
reads resolution performs (`in`, `[]`) plus `get` for consistency. The walk up
the chain is a loop, which is what keeps §5.7's stack guarantee true.

Measured consequence for the *unchanged* path: a root scope with no children
allocates one `_ScopeCache` and nothing else new, and `container.scope()` +
`await scope.close()` costs ~2.2 µs.

### 6.3 No new `Scope` tier (recorded as D-69)

The tempting alternative is `Scope.SCOPED` plus `level=` on `@injectable`. It is
rejected because:

* **it makes the level invisible in the call.** A provider's lifetime would
  depend on which *call site* opened which depth, so the same declaration would
  resolve to different instances in two places, and `compile()` — which reads
  declarations only (D-31) — could not check it;
* **it breaks the ordering rule.** `Scope` is an `IntEnum` ordered narrowest to
  widest precisely so the narrowing check is one comparison (PRD 03 §5.2). Two
  `REQUEST`-like tiers with no order between them would need a partial order,
  and a cycle of levels would become expressible;
* **it duplicates `REQUEST`.** "One instance per level" already says everything
  a level-scoped provider would say.

### 6.4 Alternatives weighed

| # | Alternative | Why not |
|---|---|---|
| 1 | Do nothing; keep `container.scope()` siblings | A child cannot share its parent's `REQUEST` instances, so the common "share the session, split the transaction" case has no spelling, and closing order stays unenforced (§1.3) |
| 2 | Add `Scope.SCOPED` + per-provider `level=` | §6.3: invisible in the call, unorderable, duplicated by `REQUEST` (D-69) |
| 3 | Inherit *values* only, keep instances per level | The measured use case is instance sharing; values-only is the sibling reading with a nicer name |
| 4 | Inherit instances by *copying* the parent's cache into the child at open | Copies break identity (two children would each hold their own copy of one instance) and make teardown ambiguous |
| 5 | An ambient `current_scope()` stack so `resolve` finds the level | Refused by D-58 for hosts, and a stack has no notion of who may close what; a child would be closable by anyone |
| 6 | `collections.ChainMap` as the cache | §6.2: breaks iteration for `finalize_scope`, changes the public annotation, and `__setitem__` semantics differ |
| 7 | Close a parent with open children by raising instead of cascading | The raise fires from `__aexit__`, replacing the body's exception; a cascade is deterministic, and the child's own `errors` stay readable (§5.7) |
| 8 | Recursive walks for close and lookup | Measured `RecursionError` at ~1,000 levels (§1.4); "unlimited" would be false at the interpreter's default limit |
| 9 | A per-subtree `max_depth` that may widen as well as narrow | A limit a callee can raise is not a limit; the container's policy would be advisory |

### 6.5 Evidence index

| Claim | Asserted by |
|---|---|
| T15-01 … T15-24 in §10.1 | `tests/integration/test_nested_scopes.py::test_…` (one test per row) |
| read-through shares the ancestor's instance | `test_a_child_reuses_the_instance_its_parent_created`, `test_read_through_is_transitive_through_every_ancestor` |
| the child caches only what it creates | `test_a_child_caches_only_what_it_creates` |
| teardown order, level by level | `test_closing_a_child_tears_down_only_what_the_child_created`, `test_closing_a_parent_closes_its_open_children_first` |
| 2,000 levels resolve and close | `test_closing_a_very_deep_chain_does_not_recurse`, `test_nesting_is_unlimited_by_default` |
| the depth errors and their `detail` keys | `test_a_container_can_cap_the_depth`, `test_a_root_scope_can_cap_the_depth_for_its_subtree`, `test_a_child_may_narrow_the_limit`, `test_a_child_may_not_widen_the_limit`, `test_an_invalid_limit_is_a_configuration_error` |
| the public `dict` seam is unchanged | `test_a_plain_dict_still_works_as_a_request_cache` |
| own-only iteration, read-through and `KeyError` on a miss | `tests/unit/test_scope_cache.py` (6 cases) |
| the surface table matches the class | `tests/docs/test_prd_03_examples.py::test_the_surface_table_names_exactly_the_shipped_members` |

No timing figure below is asserted by a test: a wall-clock number in CI is a
flake generator. Each is stated with the interpreter it was measured on, and the
*ordering* claims beside them are the parts that tests pin.

### 6.6 Measurements

Recorded on CPython 3.13.13, this tree, one registration (`Session`,
`Scope.REQUEST`), one warm-up resolution, median of five runs of 10,000
resolutions each:

| Read depth | Cache-hit `resolve` (µs) |
|---|---|
| 1 | 3.56 |
| 2 | 3.87 |
| 5 | 3.58 |
| 10 | 3.35 |
| 50 | 3.37 |
| 100 | 3.23 |
| 1000 | 3.26 |

The figure is flat because the chain is walked only when the *nearest* cache
misses; a hit at the level that asked is one `dict` lookup, and the number is
dominated by `resolve`'s own work (token binding, visibility, kwargs) rather
than by the walk. The worst case — an instance cached only at the root, resolved
at depth 1,000 — is the same flat number within noise, because 1,000 dict
membership tests are cheaper than one construction.

| Shape | Measured |
|---|---|
| `container.scope()` + `await close()`, no children | 2.16 µs |
| 5,000 empty levels | 462 B per level, 2.3 MB total (`tracemalloc`) |
| `close()` of the 5,000-level chain | 7.0 ms (1.4 µs per level) |
| first recursive cut, close or lookup | `RecursionError` at ~1,000 levels |

The per-level figure is dominated by the scope object itself plus its two small
dicts; the child list is allocated only when a scope actually opens one, which
is why the common "one scope per request" path pays nothing for nesting.

### 6.7 Risks and limits

1. **A closed child's `errors` are not the parent's.** A host that reports only
   `root.close()`'s tuple misses a child's teardown failures. Mitigation: §5.7
   and the surface table say whose errors are whose, and the alternative —
   summing tuples up the chain — would make one failure reported twice.
2. **A live child keeps its parent alive.** The parent reference is strong, by
   design (§5.2): a child may not exist without the scope it reads through. A
   chain that is never closed therefore keeps its ancestors alive; that is the
   same leak as an unclosed scope today, one level up.
3. **Read-through is O(depth) on a miss.** A token cached only at a root and
   resolved at depth *n* costs *n* membership tests. Measured flat to n = 1,000
   (§6.6), so it is not worth a memo; a future change that does memoise must keep
   §5.4's "the creating level owns it" rule.
4. **Two children of one parent are not isolated from each other's *values*.**
   They inherit a snapshot of the same parent, so both may seed the same key with
   different values; neither sees the other. Stated because "child" suggests
   isolation in some frameworks.

---

## 7. Error Catalog Impact

**No new error leaf.** Both conditions reuse leaves that PRD 05 already
publishes, with new `detail` keys:

| Condition | Leaf | `detail` | Raised by |
|---|---|---|---|
| open a child from a closed scope | `LifecycleViolationError` | `{"origin": "scope", "state": "closed", "depth": d}` | `ResolutionScope.scope` |
| open a level deeper than the limit | `LifecycleViolationError` | `{"depth": d, "max_depth": m, "reason": "depth_exceeded"}` | `ResolutionScope.scope` |
| `max_depth` / `max_scope_depth` < 1 | `LifecycleConfigError` | `{"max_depth": n, "reason": "invalid_max_depth"}` | `DIContainer.__init__`, `DIContainer.scope`, `ResolutionScope.scope` |
| a subtree widens its inherited limit | `LifecycleConfigError` | `{"max_depth": n, "inherited_max_depth": m, "reason": "widened_max_depth"}` | `ResolutionScope.scope` |

Existing rows keep their contract: resolving in a closed scope still raises
`LifecycleViolationError` with `{"token", "origin", "state"}`, and teardown
failures are still returned as `DestructError` values, never raised (D-20). The
`state` values in use are therefore `"closed"` (PRD 03 §5.3 rule 1) and, for the
new message, the `depth` of the scope that refused.

---

## 8. Phased Implementation Plan

| Phase | Work | Gate |
|---|---|---|
| P1 | `_ScopeCache` and the `parent` / `depth` / `scope` surface, read-through only | `T15-01` … `T15-09` green |
| P2 | Teardown: `child_scopes`, cascade close, per-level errors | `T15-10` … `T15-15` green |
| P3 | Depth configuration: container keyword, per-subtree narrowing, the two error shapes | `T15-16` … `T15-20` green |
| P4 | Stack safety (iterative walks) and the regression cases for the public seam | `T15-21` … `T15-24` green; full suite and `nox -s typing` green |
| P5 | Docs (`docs/guides/scopes.md`), reference regeneration, PRD/register updates | `nox -s docs`, `docs_contract`, `llms_check`, `prds` green |

Each phase is independently shippable: P1 alone is a correct (if leaky) feature,
P2 makes teardown honest, P3 adds the configuration the goal names, and P4 makes
"infinite" true rather than aspirational.

---

## 9. Documentation & Convention Updates

| Document | Change |
|---|---|
| `docs/guides/scopes.md` | the "Nested scopes" section is rewritten: the inheritance table, a `run` example that asserts read-through, the root/sibling distinction, and "Capping the depth" with its own `run` example |
| `docs/reference.md` | regenerated by `scripts/build_reference.py` from the new docstrings and signatures (the page is generated, never edited) |
| PRD 01 §5.3.3 | the `DIContainer`, `ResolutionScope` and `scope` rows carry `max_scope_depth`, `max_depth`, `parent`, `depth`, `scope`, `child_scopes` |
| PRD 03 §5.3 | the surface table gains the four members and rule 7 is restated as depth-ordered nesting rather than independent siblings |
| `README` §2, §4, §6 | this page's index row, D-64 … D-70, and the re-measured census |
| `AGENTS.md` | the documented test count, re-measured after the new cases |
| `llms.txt` / `llms-full.txt` | regenerated (`python scripts/build_llms_full.py`) so the corpus includes the new section |

Conventions this page keeps: every `python` fence carries a marker (`run`
executes in the corpus test, `fragment` is deliberately partial), no framework
import appears on a specification page (D-63), and no sentence names a member
the class does not ship — `tests/docs/test_prd_03_examples.py` reads both the PRD
03 table and its prose.

---

## 10. Test Plan

### 10.1 Enumerated test cases

T15-01 … T15-23 live in `tests/integration/test_nested_scopes.py`, T15-25 and
T15-26 in `tests/unit/test_scope_cache.py`, and T15-24 in `tests/docs/`. Every
case drives the public surface through `helpers.run` (the suite does not use a
pytest-asyncio marker); only the cache contract's own module reaches for the
private class, because own-only iteration is invisible from outside.

| ID | Case | Test |
|---|---|---|
| T15-01 | a scope opens a child; `parent` and `depth` are the chain | `test_a_scope_can_open_a_child_scope` |
| T15-02 | a child is an async context manager of itself | `test_a_child_is_an_async_context_manager_of_itself` |
| T15-03 | a child reuses the parent's `REQUEST` instance | `test_a_child_reuses_the_instance_its_parent_created` |
| T15-04 | a child caches only what it creates | `test_a_child_caches_only_what_it_creates` |
| T15-05 | read-through is transitive through every ancestor | `test_read_through_is_transitive_through_every_ancestor` |
| T15-06 | `SINGLETON` is the same object at every level | `test_a_singleton_is_the_same_object_at_every_level` |
| T15-07 | `TRANSIENT` is fresh at every level | `test_a_transient_is_fresh_at_every_level` |
| T15-08 | values are copied from parent to child, transitively | `test_values_are_copied_from_parent_to_child` |
| T15-09 | a child overrides a value without mutating the parent's | `test_a_child_overrides_a_parent_value_without_mutating_it` |
| T15-10 | a `value_tokens` seed reaches a child | `test_a_value_token_can_be_supplied_at_the_root_and_read_by_a_child` |
| T15-11 | closing a child tears down only what it created | `test_closing_a_child_tears_down_only_what_the_child_created` |
| T15-12 | a generator provider closes at the creating level | `test_a_generator_provider_is_torn_down_at_the_level_that_created_it` |
| T15-13 | a parent close cascades, children first | `test_closing_a_parent_closes_its_open_children_first` |
| T15-14 | resolving through a cascade-closed child raises `state: closed` | `test_resolving_in_a_child_after_the_parent_closed_raises` |
| T15-15 | opening a child from a closed scope raises | `test_opening_a_child_from_a_closed_scope_raises` |
| T15-16 | nesting is unlimited by default (100 levels) | `test_nesting_is_unlimited_by_default` |
| T15-17 | a container caps the depth with the documented `detail` | `test_a_container_can_cap_the_depth` |
| T15-18 | a root scope caps its own subtree | `test_a_root_scope_can_cap_the_depth_for_its_subtree` |
| T15-19 | a child may narrow the limit | `test_a_child_may_narrow_the_limit` |
| T15-20 | a child may not widen it; `max_depth=0` is a config error | `test_a_child_may_not_widen_the_limit`, `test_an_invalid_limit_is_a_configuration_error` |
| T15-21 | a 2,000-level chain closes without recursion | `test_closing_a_very_deep_chain_does_not_recurse` |
| T15-22 | a plain `dict` still works as `request_cache` | `test_a_plain_dict_still_works_as_a_request_cache` |
| T15-23 | strict mode accepts a child as an active scope | `test_strict_mode_accepts_a_child_scope_as_an_active_scope` |
| T15-24 | the surface table names exactly the shipped members | `tests/docs/test_prd_03_examples.py` |
| T15-25 | the cache is own-only when iterated and read-through when read | `tests/unit/test_scope_cache.py::test_iteration_len_and_membership_of_own_entries_are_own_only`, `…::test_a_cache_reads_through_to_its_parent`, `…::test_a_miss_raises_keyerror_rather_than_returning_none`, `…::test_a_write_in_the_child_does_not_reach_the_parent` |
| T15-26 | a collected or closed child is pruned, not leaked into `child_scopes()` | `tests/unit/test_scope_cache.py::test_a_collected_child_is_pruned_from_child_scopes`, `…::test_a_closed_child_is_pruned_from_child_scopes` |

### 10.2 Gates

| Gate | What it proves here |
|---|---|
| `nox -s unit integration` | the T15 rows, plus every existing scope/lifecycle case unchanged |
| `nox -s typing` (mypy `--strict`, pyright) | `_ScopeCache`'s overrides and the new signatures are typed; the mypy fixtures keep the `resolve` inference |
| `nox -s coverage` | the new branches (`depth` guards, cascade, `_live_children` pruning) are covered, not merely present |
| `nox -s docs_contract` | the regenerated reference page matches the library; every python fence in this page compiles and is marked |
| `nox -s docs` (`mkdocs build --strict` + the rendered-page check) | the page renders whole |
| `nox -s prds` | this page's ten sections, its citations, its decisions and the census row agree |
| `nox -s llms_check` | `llms-full.txt` carries the regenerated section |

### 10.3 Acceptance traceability

| Goal | Cases |
|---|---|
| G1 | T15-01, T15-02 |
| G2 | T15-03 … T15-10 |
| G3 | T15-11 … T15-15 |
| G4 | T15-16 … T15-20 |
| G5 | T15-21, and the depth table in §6.6 |
| G6 | T15-22, T15-23, T15-24, plus the unchanged `tests/integration/test_scopes_and_lifecycle.py` |
