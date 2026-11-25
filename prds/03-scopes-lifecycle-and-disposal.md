# PRD 03 — Scopes, Lifecycle & Disposal

| Field | Value |
|-------|-------|
| Status | Draft |
| Owner PRD | 03 |
| Depends on | 01 (symbol surface), 02 (registration, `Provider`, lazy compile) |
| Depended on by | 05 (error catalogue), 06 (visibility), 07 (overrides), 08 (migration) |
| Reference implementation | `../lauren-framework/lauren/_di/{__init__,_scope}.py`, `lauren/_lifecycle/__init__.py`, `lauren/context.py`, `lauren/types.py` |

This PRD specifies what a lifetime *means* once a provider has one: which scope may consume
which, what an explicit request scope is and who owns its cache, when each lifecycle hook fires
and in what order, who drives a singleton from creation to destruction without a framework, and
how teardown reports failure without losing the rest of the work.

## 1. Problem Statement

Today, in `lauren`, this subject is spread over four modules and none of them is authoritative:

| Concern | Current home | Lines | Why it is a problem here |
|---------|--------------|-------|--------------------------|
| Scope ordering and the narrowing rule | `types.py` (`Scope`, 50 lines of docstring) + `_di/__init__.py::compile` (`_check_scope`) | 90 | The rule is a comparison, but *which* edges it applies to (params, fields, `list[T]` members, aliases) is only visible in the traversal |
| The request cache | `_di/__init__.py` (`request_cache` parameter) + `context.py::OperationScope` | 120 | The cache is created by an object the DI package does not own, so `lauren._di` alone cannot state its lifetime |
| Hook collection and MRO ordering | `_di/__init__.py::_find_lifecycle_hooks` | 36 | Ordering is three rules in one loop (MRO direction, per-class definition order, dedup) and is documented only in a docstring |
| Hook invocation | `_lifecycle/__init__.py::LifecycleScheduler` (singletons) **and** `_di/__init__.py::_instantiate` (request/transient) **and** `_di/_scope.py::finalize_scope` (request teardown) | 200 across three files | Three implementations of "run the hooks", with different error handling and — for sync hooks — different threading |

That split is invisible to a framework user, who only ever sees `app.startup()` and a request.
A standalone library has no `app`, so every seam the framework papered over becomes API surface
this PRD has to define.

### 1.1 The narrowing rule is one comparison, but the edges are not obvious

`Scope` is an `IntEnum` deliberately ordered narrowest-to-widest (`TRANSIENT = 0`, `REQUEST = 1`,
`SINGLETON = 2`). The compile-time check is one line:

```python fragment
if for_scope > chosen.scope:
    raise DIScopeViolationError(...)
```

What is *not* one line: `for_scope` is the **consumer's** scope, and the check runs on every edge
the graph has — constructor parameters, function-provider parameters, class-body field
dependencies, and each member of a `list[T]` multi-binding. A PRD that said only "a singleton may
not depend on a request-scoped provider" would leave three of those four unchecked in a reader's
mind. §5.2 states the rule once and then enumerates the edges it applies to.

### 1.2 Nothing owns the request cache

Resolving a `REQUEST`-scoped provider needs somewhere to put the instance. The reference threads a
bare `dict` through `resolve(..., request_cache=...)`, and the object that creates it lives in
`lauren/context.py` — a module about *application* context, which this library does not ship.

Two things follow, and both must be decided here rather than inherited:

1. `lauren-di` needs a scope object it can own: created by `container.scope()`, holding the cache,
   exposing `resolve`, and closing the cache on exit (PRD 01 §5.3.3 already fixes the name,
   `ResolutionScope`).
2. The low-level `request_cache` parameter must stay (`lauren` passes it today), so the PRD has to
   say which of the two is the supported contract and which is the seam.

### 1.3 Without strict mode, request scope silently degrades

The reference's `DIContainer(strict_request_scope=False)` is the default. Resolving a
`REQUEST`-scoped provider with no active request does not fail: it constructs a fresh instance per
resolution, caches it nowhere, and logs one warning per provider. Verified:

```
lax:    resolve(Req) is resolve(Req)  -> False      # never cached
        container.singletons()        -> {}          # never stored
strict: DIScopeViolationError detail={'token': 'Req', 'reason': 'no_active_scope'}
```

That is a defensible default for a framework that wants an app to boot without wiring a scope, and
a poor default for a library whose only job is lifetime correctness. But PRD 01 already fixes the
constructor default (`strict_request_scope: bool = False`, parity), so this PRD must specify both
modes exactly — including what the lax mode loses — and recommend strict mode in its own docs.

### 1.4 Three bugs in the reference's singleton lifetime

Probed directly, with a trivial singleton that appends to a list in each hook:

| Probe | Reference result | Consequence |
|-------|------------------|-------------|
| `resolve(S)` before `startup()` | `['S.init']` — no `S.post` | correct, but undocumented: a singleton resolved early is *not* initialised |
| `startup()` twice | `['S.post', 'S.post']` — hook fired twice | `run_post_construct` is not idempotent; only `resolve` is guarded by `mark_singleton_initialized` |
| `shutdown()`, then `resolve(S)` | returns the **same, already-destructed** object; `singletons()` still populated | zombie instances: `@pre_destruct` closed the resource, the container hands it back |

The third is the serious one. A library cannot ship a container that returns a destroyed singleton;
§5.6 and D-22 fix it by making `shutdown()` clear the cache and the container restartable.

### 1.5 Teardown is not one algorithm

The reference tears down two kinds of cache in three different ways:

| Cache | Implementation | Order | Sync-hook handling | Errors |
|-------|----------------|-------|--------------------|--------|
| Request cache | `finalize_scope` (`_scope.py`) | reverse insertion, then per instance `@pre_destruct` → `aclose()` | called inline on the loop | collected, returned as `list[DestructError]` |
| Singleton hooks | `LifecycleScheduler.run_pre_destruct` (pass 1) | reverse topological, per provider | **`asyncio.to_thread` + `asyncio.wait_for(timeout)`** | collected, returned as `list[Exception]` |
| Singleton generators | `LifecycleScheduler.run_pre_destruct` (pass 2) | reverse topological, a second full loop | n/a | collected |

So a singleton's `@pre_destruct` may run off-thread with a 10-second timeout, while the very same
hook on a request-scoped instance runs inline with none — and a singleton generator's teardown runs
after *every* hook, not after its own. Verified ordering for one request scope:

```
resolved A, then B(A), then gen     ->  teardown order: gen.teardown, B.pre, A.pre
```

which is the right answer for the request cache and the wrong shape for singletons. PRD 03
specifies **one** algorithm (§5.7) and applies it to both caches (D-24).

### 1.6 The error surface is not usable from the public API

`LifecycleScheduler.run_pre_destruct` returns `list[Exception]`, and the reference's only caller
(`LaurenApp.shutdown`) logs them. Nothing a test can assert. `OperationScope` does expose
`teardown_errors` (shipped here as `ResolutionScope.errors`, §5.3), which is the right shape —
but only for request scopes. PRD 01 originally specified
`shutdown() -> None`, which would have left the singleton half of "teardown never aborts the rest"
untestable, and `LifecycleViolationError` was in PRD 01's *not carried* list even though a scope
object needs exactly that error. Both are corrected here (D-20, D-23), and PRD 01 §5.3.3 and §7 now
reflect the corrections.

## 2. Goals

| ID | Goal | Verification |
|----|------|--------------|
| G1 | `Scope` is an `IntEnum` ordered `TRANSIENT < REQUEST < SINGLETON`, with `.label` and a stable `__str__` | `.label == "singleton"`, `str(Scope.REQUEST) == "request"` |
| G2 | The narrowing rule is one stated comparison, and the PRD enumerates every edge it applies to | 9-cell state table (§5.2) covered by tests T03-05…T03-13 |
| G3 | `container.scope()` returns a `ResolutionScope` that owns its cache, is single-use, and closes it on exit | §5.3 rules; T03-14…T03-22 |
| G4 | Strict and lax request-scope resolution are both specified, including what lax mode loses | §5.4 table; T03-23…T03-25 |
| G5 | `@post_construct` / `@pre_destruct` ordering is stated as three rules (MRO direction, per-class definition order, override/inheritance behaviour) | §5.5 table; T03-26…T03-32 |
| G6 | Singleton hooks are fired by `container.startup()` / `shutdown()`; no scheduler, no app object | §5.6; T03-33…T03-38 |
| G7 | Teardown is one algorithm: reverse creation order, `@pre_destruct` chain then `aclose()`, per step containment | §5.7; T03-39…T03-45 |
| G8 | `DestructError` aggregation never aborts the remaining teardown and is observable through the public API | §5.8, D-20; T03-46…T03-50 |
| G9 | Container lifecycle is idempotent and restartable: `shutdown()` clears the cache, the next `startup()` reinitialises | §5.10, D-22; T03-51…T03-54 |
| G10 | Every rule above is testable with `lauren_di` imports only — no private attribute, no `app`, no `LifecycleScheduler` | §10.1 uses only public names; §10.3 maps each goal |

## 3. Non-Goals

- **A lifecycle scheduler, event loop policy, signal handling or background-service runner.** The
  reference's `LifecycleScheduler` also starts hosted services, drains HTTP connections and honours
  a drain timeout. All of that is application lifecycle, not DI lifecycle, and stays in `lauren`
  (PRD 01 §3, PRD 08).
- **Per-hook timeouts.** The reference gives singleton hooks a `timeout=10.0` default and raises
  `DestructTimeoutError`. PRD 01 already drops `DestructTimeoutError`; this PRD states the
  consequence (D-21) and the workaround: `asyncio.wait_for(container.shutdown(), n)`.
- **Implicit thread offload for sync hooks.** `to_thread` moves user code across threads, which
  breaks hooks that touch loop-bound state and makes ordering observable-but-surprising. v1 calls
  sync hooks inline (D-21).
- **`Scope.TRANSIENT` teardown.** A transient instance is never tracked, so it has no teardown
  (D-17 already forbids transient generator providers for the same reason).
- **Request-scope propagation across `asyncio` tasks or threads.** A `ResolutionScope` may be
  shared by concurrent tasks (documented), but the library does not create a context variable
  implicitly; an embedder that wants per-task scopes creates one per task. ContextVars for
  override *activation* are PRD 07, not this PRD.
- **Nested scopes or scope inheritance.** A request scope inside a request scope is just another
  cache; the inner one does not see the outer one's instances.
- **HTTP, WebSocket or ASGI correlation.** "Request scope" here means "the lifetime of one
  `ResolutionScope`". Nothing maps it to a transport.

## 4. Prior Art

| System | Its lifetime model | What `lauren-di` takes, and what it does not |
|--------|--------------------|-----------------------------------------------|
| NestJS | Three fixed scopes (`DEFAULT`/`REQUEST`/`TRANSIENT`); request-scoped providers nest a DI subtree per request; lifecycle hooks `OnModuleInit`/`OnModuleDestroy`/`OnApplicationShutdown` | The three-scope vocabulary and hook names are the model. NestJS's per-request subtree and module lifecycle are out of scope (no modules in v1, PRD 06) |
| `dishka` | Scope *graph* with explicit enter/exit and per-scope keys; a dependency's scope is declared on the factory | Confirms that an explicit scope context manager owning its cache is the right shape (§5.3). `dishka`'s scope stack is richer than one level, which we explicitly do not do |
| `punq` | Registration scopes (`Instance`, `Singleton`, `Transient`) with no teardown at all | Confirms three scopes are enough; confirms that "no teardown" is a real gap users hit |
| Spring | `singleton` / `prototype` / `request` / `session`; `@PostConstruct` / `@PreDestroy`; teardown via `DisposableBean` | The hook names, and the rule that destruction is reverse of creation. Spring's `@PreDestroy` on request beans is also per-scope |
| .NET `Microsoft.Extensions.DependencyInjection` | `Transient`/`Scoped`/`Singleton`; the container validates nothing about scope capture | The negative lesson: .NET's captured-dependency bug (singleton holding a scoped service, silently promoted) is exactly what `DIScopeViolationError` prevents at compile time |
| FastAPI `yield` dependencies | Setup before `yield`, teardown after, newest-first exit; exceptions in teardown propagate and surface as 500s | The `yield`-as-boundary semantics this library inherits (PRD 02 §5.8) and the reverse-order teardown. The difference: FastAPI *raises* the first teardown error, which loses the rest — §5.8 explains why we collect instead |

## 5. Proposed API

### 5.1 `Scope`

```python fragment
from enum import IntEnum

class Scope(IntEnum):
    """Lifetime width, narrowest to widest. The numeric order is the narrowing rule."""

    TRANSIENT = 0
    REQUEST = 1
    SINGLETON = 2

    @property
    def label(self) -> str:
        """Stable lowercase name: "transient", "request", "singleton"."""
        return self.name.lower()

    def __str__(self) -> str:          # stable across Python versions
        return self.label
```

Contract:

| Property | Rule |
|----------|------|
| Ordering | `TRANSIENT < REQUEST < SINGLETON` is the **definition** of narrower/wider (§5.2) |
| `.label` | lowercase name; used in every message and `detail` value, never `str(value)` of the enum |
| `str(scope)` | `"request"`, not `"Scope.REQUEST"` and not `"1"`; `repr` stays `Scope.REQUEST` |
| Identity | `Scope.SINGLETON is Scope.SINGLETON`; comparisons against `int` work (`Scope.REQUEST == 1`) — that is what `IntEnum` buys and it is relied on by stored metadata |
| Membership | Exactly these three members in v1; a fourth is a breaking change to the ordering rule |

What "width" means operationally:

| Scope | Instance count | Cache | Teardown |
|-------|----------------|-------|----------|
| `TRANSIENT` | one per resolution | none | none (never tracked) |
| `REQUEST` | one per active `ResolutionScope` | the scope's dict | at scope close |
| `SINGLETON` | one per container lifetime | the container's dict | at `shutdown()` |

### 5.2 The narrowing rule

**Rule.** For every dependency edge `consumer -> dependency`:

```
reject  iff  consumer.scope > dependency.scope
```

Read out in words: a consumer may depend on anything **as wide as or wider than** itself. A
narrower dependency inside a wider consumer would have to outlive its own lifetime — a singleton
holding a request-scoped instance would promote it to a singleton and leak it past the request that
owns it.

**The nine cells.** Rows are the consumer, columns the dependency. The six off-diagonal cells are
the pairs that need a decision: three widenings (allowed) and three narrowings (error).

| Consumer ↓ / Dependency → | `TRANSIENT` (0) | `REQUEST` (1) | `SINGLETON` (2) |
|---------------------------|-----------------|---------------|-----------------|
| **`TRANSIENT`** (0) | allowed — each consumer construction gets a fresh dependency | allowed (widen) — the dependency comes from the active scope's cache | allowed (widen) — the shared singleton |
| **`REQUEST`** (1) | **`DIScopeViolationError`** (narrow) | allowed — one instance per scope, shared by the consumer | allowed (widen) — the shared singleton |
| **`SINGLETON`** (2) | **`DIScopeViolationError`** (narrow) | **`DIScopeViolationError`** (narrow) | allowed — one instance per container |

Verified against the reference for all nine cells (`compile()` on a two-provider container):

```
T->T ok | T->R ok | T->S ok
R->T DIScopeViolationError(request/transient)  | R->R ok | R->S ok
S->T DIScopeViolationError(singleton/transient) | S->R DIScopeViolationError(singleton/request) | S->S ok
```

**Which edges are checked.** All four kinds, or the rule is only half enforced:

| Edge | Included? | Reference evidence |
|------|-----------|--------------------|
| Constructor / `__new__` parameters (`provider.deps`) | yes | `compile` walks `provider.deps` |
| Function-provider parameters (`provider.deps`) | yes | verified: a singleton `@injectable()` function taking a request-scoped parameter raises at `compile()` |
| Class-body field dependencies (`provider.field_deps`) | yes | verified: a singleton with `d: RequestScoped` raises at `compile()` |
| Each member of a `list[T]` multi-binding | yes, per member | `resolve_one` calls `_check_scope` inside its `list[T]` loop (PRD 04) |
| An edge through an `existing` alias | yes, judged by the **terminal** target's scope | `_alias_terminal` walks the chain before comparing; a dangling link or a cycle keeps its own error instead of becoming a scope violation (D-27, PRD 04 §5.5 rule 2) |
| A `values`-seeded token (no provider) | no — there is no provider to have a scope | `framework_tokens` are skipped |
| Parameters satisfied by a default while no provider exists | no — no edge | `compile`'s `defaults.get(name)` escape |

**When it is checked.** At `compile()`, and therefore at the first `resolve()` (D-14: a library
cannot require an explicit `compile()`). Resolution after a successful compile never re-checks, so
the cost is one traversal (PRD 05 §5 owns the traversal itself and its other checks).

**What it raises.** `DIScopeViolationError`, a `DIError` leaf:

| Aspect | Value |
|--------|-------|
| Message | `"{Consumer label} depends on {dependency label}-scoped {dependency}"`, e.g. `"Singleton depends on request-scoped ReqDep"` |
| `detail.dependent_scope` | `consumer.scope.label` |
| `detail.dependency_scope` | `dependency.scope.label` |
| `detail.dependency` | `_describe(token)` |
| `detail.alias` | `_describe(alias)` — present only when the edge went through an `existing` alias, so a caller can tell which indirection to remove |
| Raised by | `compile()`, `resolve()` (via lazy compile) |

**The escape hatch is not a loop-hole.** A provider that genuinely needs per-request data should be
`REQUEST`-scoped itself; a singleton that needs a value from a request takes it as a *parameter to a
method*, not through injection. There is no `Scope.BYPASS`: a scoped escape hatch would recreate the
.NET captured-dependency bug with extra steps.

### 5.3 `container.scope()` and `ResolutionScope`

The scope object is the only supported owner of a request cache.

```python run
import asyncio
from lauren_di import DIContainer, Scope, injectable, pre_destruct

@injectable(scope=Scope.REQUEST)
class Tx:
    rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True

@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    def __init__(self, tx: Tx) -> None:
        self.tx = tx

    @pre_destruct
    async def rollback(self) -> None:
        await self.tx.rollback()

@injectable(scope=Scope.REQUEST)
class UserRepo:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

async def main() -> None:
    container = DIContainer()
    container.register(UserRepo)
    container.register(UnitOfWork)
    container.register(Tx)

    async with container.scope(values={"REQUEST_ID": "r-1"}) as scope:
        repo = await scope.resolve(UserRepo)      # uow is cached in this scope
        again = await scope.resolve(UserRepo)
        assert repo is again
        assert scope.values["REQUEST_ID"] == "r-1"
        assert repo.uow.tx is await scope.resolve(Tx)
        uow = repo.uow
    # leaving the block tore down UserRepo, UnitOfWork and Tx, newest first
    assert scope.closed is True
    assert scope.errors == ()
    assert uow.tx.rolled_back is True

asyncio.run(main())
```

`ResolutionScope` surface:

| Member | Signature | Contract |
|--------|-----------|----------|
| `resolve` | `async resolve(token: Any) -> Any` | Resolves within this scope. `REQUEST` instances cache per token; `SINGLETON`s come from the container; `TRANSIENT`s are fresh. Raises `LifecycleViolationError` unless the scope is active |
| `values` | `Mapping[Any, Any]` | Read-only view of the seeds passed to `container.scope(values=...)`. A token found here is returned as-is by `resolve`, and satisfies a constructor parameter of that type (D-8/D-13) |
| `parent` | `ResolutionScope \| None` | The scope this one was opened from, `None` for a root scope (PRD 15 §5.2) |
| `depth` | `int` | How many levels deep this scope sits; a root scope is `1` (PRD 15 §5.2) |
| `scope` | `scope(*, values: Mapping[Any, Any] \| None = None, max_depth: int \| None = None) -> ResolutionScope` | Opens a child scope that reads through to this one (PRD 15 §5.3) |
| `child_scopes` | `child_scopes() -> tuple[ResolutionScope, ...]` | The child scopes opened from this one that nobody has closed yet (PRD 15 §5.7) |
| `closed` | `bool` | `False` until the scope closes, `True` after; the reference spells this `active`, with the opposite sense |
| `errors` | `tuple[DestructError, ...]` | Empty until the scope closes; then the aggregated failures from that close (D-20) |
| `close` | `async close() -> tuple[DestructError, ...]` | Idempotent. Tears down the cache newest-first and returns the errors. A second call returns the same tuple and does nothing |
| `__aenter__` / `__aexit__` | async context manager | `__aenter__` activates; `__aexit__` calls `close()` and **propagates the body's exception unchanged** |

`container.scope()` is annotated `-> ResolutionScope` (PRD 01 §5.3.3): the scope object is itself
the async context manager — `__aenter__` returns `self`, `__aexit__` calls `close()` — so
`async with container.scope() as scope:` is unchanged and the result still satisfies
`AsyncContextManager[ResolutionScope]`, while a caller that assigns it keeps `values`,
`errors` and `close()` typed.

Rules, each testable from the public surface:

1. **Single use.** `PENDING -> ACTIVE -> CLOSED`, one transition each way. Entering twice, or
   entering after close, raises `LifecycleViolationError` with `detail={"state": ...}`, where
   `state` is the state at the moment of the call — `"active"` for a second enter of a live scope,
   `"closed"` for an enter after close. The reference also puts the scope's name in
   `detail["scope"]`; `ResolutionScope` has no name, so that key is dropped (§6.4 #10).
2. **Nothing resolves outside the block.** `resolve()` while `PENDING` or after `CLOSED` raises
   `LifecycleViolationError`. This is the guard that makes "the cache only exists inside the
   `async with`" true rather than conventional.
3. **The scope owns the dict.** `container.scope()` creates the cache, hands it to
   `resolve(request_cache=...)` internally, and clears it on close. `resolve(request_cache=...)`
   remains public for embedders that already own a per-request dict (`lauren` does), but it is the
   low-level seam: it performs no teardown, so an embedder using it must call the same teardown
   itself. One paragraph, one sentence of warning — but stated.
4. **`close()` before entry is a no-op**, so `try/finally: await scope.close()` is always safe. It
   marks the scope `CLOSED`; a later `__aenter__` then raises per rule 1.
5. **`close()` never raises** for teardown failures (§5.8). It *does* raise nothing else either: a
   `BaseException` from a hook (e.g. `CancelledError`) propagates (rule in §5.8).
6. **Concurrency is documented, not prevented.** Two coroutines sharing one scope may both observe
   a cache miss for the same token and construct two instances; the second store wins. This is the
   same behaviour as a request in the reference and is stated so nobody discovers it in production.
7. **Nesting is depth-ordered.** `scoped.scope()` opens a child scope (PRD 15): its cache reads
   through to its ancestors, an instance it creates is cached and torn down at its own level, and
   closing a scope closes the children nobody closed first. `container.scope()` inside an open
   scope is still available and is a *root* — a new chain with an empty cache and no inherited
   values, which is what the sibling reading in PRD 15 §5.9 calls the escape hatch.

### 5.4 Strict vs lax request-scope resolution

When a `REQUEST`-scoped provider is resolved with no active scope (a bare
`container.resolve(T)`), the two modes differ:

| | Lax (default) | Strict |
|---|---|---|
| Constructor | `DIContainer(strict_request_scope=False)` | `DIContainer(strict_request_scope=True)` |
| Behaviour | Constructs a fresh instance and caches it **nowhere**; a second resolve constructs a second instance | Raises before constructing anything |
| Observable | `logging.getLogger("lauren_di")` warning, **once per token** (`WARNING`) | `DIScopeViolationError`, `detail={"token": ..., "reason": "no_active_scope"}` |
| Lifetime | Like `TRANSIENT` — but the provider still says `REQUEST` | — |
| Teardown | None: nothing references the instance, so `@pre_destruct` and `aclose()` never run | — |
| Generator providers | The wrapper is created and immediately dropped; teardown never runs | — |

Why the default stays lax: PRD 01 fixes the signature for parity with `lauren`, whose apps boot
without a scope and resolve request-scoped providers only inside requests. Changing a default is a
silent behaviour change for every embedder, which D-2 forbids. The library's own documentation
(`docs/guides/scopes.md`, §9) recommends `strict_request_scope=True` for new code.

Both modes share one invariant: **`REQUEST` means "one per scope", never "one per resolve"**. In lax
mode the honest reading is "there was no scope, so there is no instance to speak of" — which is why
the warning says the instance will never be torn down, not that it was cached.

### 5.5 `@post_construct` and `@pre_destruct`

```python fragment
from lauren_di import injectable, post_construct, pre_destruct

@injectable()
class Pool:
    @post_construct
    async def open(self) -> None:          # after construction, before first use
        self.conn = await connect(self.dsn)

    @pre_destruct
    async def close(self) -> None:         # during teardown
        await self.conn.close()
```

| Aspect | Rule |
|--------|------|
| Target | A method. The decorator only checks `callable(fn)`; a non-callable raises `LifecycleConfigError` (`"@post_construct must wrap a method"`). A class or a plain function **is** callable and is silently marked — a documented limitation, not a detection |
| Return | The function unchanged (`post_construct(f) is f`); the marker is an attribute on the function, so `@classmethod`/`@staticmethod` ordering matters — decorate the outermost callable |
| Call shape | Bound to the instance, no arguments: `getattr(instance, hook.__name__)()`. The name must therefore be the attribute name; renaming the attribute at runtime breaks the lookup (contained as a teardown error, §5.8) |
| Sync or async | Both. A returned awaitable is awaited; anything else is ignored |
| Multiple per class | Allowed; every marked method runs, in the order below |
| Overriding | An override **without** the decorator drops the base hook (attribute lookup finds the subclass's function, which has no marker). Verified: `class Sub(Base)` redefining `setup` without `@post_construct` yields `post_construct_chain == ()` |
| Inheriting | An inherited method keeps its hook: a subclass that does not redefine `setup` still runs `Base.setup` |
| Dunders | Never hooks. Names matching `__*__` are skipped even if marked (they may be deprecated class attributes) |
| Same function under two names | Runs once (dedup by the underlying function object) |
| On a function provider, or a plain `register_value` provider | Impossible: hooks are collected from a class. `post_construct_chain` stays empty |
| On an object passed to `register_instance` / `adopt_lifecycle` | **Possible** — the one exception: hooks (decorated, or the plain `startup()`/`aclose()` fallback) come from `type(instance)` (§5.9.1) |

**Ordering, three rules.** Let `MRO(cls) = [Leaf, Middle, Root, object]`.

| Rule | `@post_construct` (startup) | `@pre_destruct` (teardown) |
|------|-----------------------------|----------------------------|
| 1. MRO direction | **bases first**: `Root`, `Middle`, `Leaf` | **subclasses first**: `Leaf`, `Middle`, `Root` |
| 2. Within one class | definition order (`__dict__` order) | definition order |
| 3. Overrides | a redefinition without the marker removes the base hook; one function runs once even if reachable twice | same |

Verified on `Base(b1, b2)` + `Sub(s1)`:

```
post_construct_chain -> ['Base.b1', 'Base.b2', 'Sub.s1']     # bases first, definition order
pre_destruct_chain   -> ['Sub.t1', 'Sub.t2']                 # subclasses first, definition order
```

The symmetry is the point: initialisation flows root→leaf (a base's invariants before a leaf's), and
destruction flows leaf→root (a leaf releases what it added before the base tears down what it
provided). This is the same rule Spring and NestJS use, and the reverse of it is a class of bug
("destroyed my connection pool before the repository that used it closed").

**When each hook fires** — the table that makes the whole lifetime model checkable:

| Event | `@post_construct` | `@pre_destruct` |
|-------|-------------------|-----------------|
| `startup()` | every singleton, in dependency order (deps first) | — |
| `resolve(SINGLETON)` **before** `startup()` | **not** fired | — |
| `resolve(SINGLETON)` **after** `startup()` | not re-fired | — |
| `resolve(REQUEST)` / `resolve(TRANSIENT)` | at construction of that instance | — |
| scope close | — | every cached instance, newest first |
| `shutdown()` | — | every singleton, reverse creation order |
| `resolve()` after `shutdown()` | not fired until the next `startup()` | — |

The reference does exactly this (verified: `resolve(S)` yields `['S.init']`; `startup()` then yields
`['S.post']`), with one exception that D-22 fixes: a second `startup()` re-fires every hook.

### 5.6 Who fires singleton hooks: `container.startup()` / `shutdown()`

There is no `LifecycleScheduler` and no application object (D-7). The container is the lifecycle
owner, and an embedder calls two methods.

```python fragment
container = DIContainer()
container.register(Pool)
container.register(Repo)          # Repo(pool: Pool)

await container.startup()         # constructs every singleton, deps first, then @post_construct
try:
    ...
finally:
    errors = await container.shutdown()   # reverse creation order; never raises for hook failures
```

`async startup() -> None` steps:

| # | Step | Notes |
|---|------|-------|
| 1 | If already started, return immediately | idempotent (D-22); a second call fires nothing |
| 2 | Ensure the graph is validated | lazy `compile()` (D-14); raises `CircularDependencyError`, `MissingProviderError`, `DIScopeViolationError`, `ProtocolAmbiguityError` |
| 3 | Order providers topologically (dependencies first) | edges are `deps` + `field_deps`; independent providers keep registration order; `provider_kind == "value"` providers have no edges and sort first |
| 4 | For each `SINGLETON` provider, `await resolve(token)` | this constructs it (and its dependencies) if it does not exist yet |
| 5 | Run `post_construct_chain` in order, awaiting awaitables | skip providers whose hooks already ran (guarded by an initialised set) |
| 6 | Mark the container started | — |

A failure at step 4 or 5 triggers **rollback**: every singleton created so far is torn down
newest-first with the §5.7 algorithm, teardown failures are logged and attached as
`DestructError`s (they do not replace the original), the cache is cleared, and the original
exception propagates unchanged.

`async shutdown() -> tuple[DestructError, ...]` steps:

| # | Step | Notes |
|---|------|-------|
| 1 | If nothing is started and the cache is empty, return `()` | idempotent (D-22) |
| 2 | Snapshot the singleton cache, newest-created first | insertion order is creation order |
| 3 | Run the §5.7 teardown algorithm over that snapshot | `@pre_destruct` chain, then `aclose()` |
| 4 | Clear the cache and the initialised set; mark not started | the container is now restartable (§5.10) |
| 5 | Return the aggregated `tuple[DestructError, ...]` | never raises for hook failures (D-20) |

Notes that fall out and are worth stating:

- `startup()` **eagerly constructs** every singleton, including ones nothing has resolved yet.
  That is what "startup" means: the container's singletons are all live or the container is not
  started. It also means a `MissingProviderError` surfaces at `startup()` rather than at first use.
- Because `startup()` constructs in dependency order, a dependency's `@post_construct` has already
  run when its consumer's hook runs. This is the only ordering guarantee hooks may rely on.
- `shutdown()` does not touch open `ResolutionScope`s. A scope left open at container shutdown keeps
  its cache; the embedder must exit its scopes. Documented, not tracked: tracking would need a
  registry keyed by scope, with lifetime questions of its own.
- Nothing is destroyed implicitly. If an embedder never calls `shutdown()`, `@pre_destruct` never
  runs — a `try/finally` or an async context manager owned by the embedder is the contract.

### 5.7 The teardown algorithm

One algorithm, applied to two caches. `tear_down(entries, origin)` where `entries` is an ordered
snapshot of `(key, instance)` pairs:

| Step | Action | On failure |
|------|--------|-----------|
| 1 | Take the snapshot in **reverse creation order** (newest first). A live cache is never iterated | — |
| 2 | For each `(key, instance)`: resolve the provider from `instance_index()`; `name` = the token's description, or the instance's type for a multi-binding slot | unknown key → provider `None`, `name` = key, hooks = `()` |
| 3 | For each hook in `provider.pre_destruct_chain` (subclasses first, definition order): call `getattr(instance, hook.__name__)()`, await if awaitable | log `ERROR` with traceback, append `DestructError(stage="pre_destruct")`, **continue with the next hook** |
| 4 | Then, if `getattr(instance, "aclose", None)` is callable: call it and await if awaitable. For a generator provider this is the wrapper that advances past `yield` | log, append `DestructError(stage="aclose")`, continue with the next instance |
| 5 | After the last instance, return the `DestructError` list in the order produced (newest instance first) | — |

Properties this buys, each one a test:

| Property | Consequence |
|----------|-------------|
| Reverse creation order | A consumer is torn down before its dependency. Within one instance, hooks run leaf→root |
| Per-step containment | One exploding hook does not skip the remaining hooks of the same instance, nor the remaining instances |
| Per-instance ordering | `@pre_destruct` runs **before** `aclose()` on the same instance (a generator provider's code-after-`yield` runs after the class's hook, not before) |
| Idempotence at the call site | `scope.close()` twice and `shutdown()` twice both return the same result; the second tears nothing down |
| No exceptions for hook failures | The caller gets a tuple; nothing is re-raised (§5.8) |
| `Exception` vs `BaseException` | Only `Exception` subclasses are contained. `KeyboardInterrupt`, `SystemExit` and `asyncio.CancelledError` propagate and abort the rest — cancellation must not be swallowed |

The reference splits this into three implementations (§1.5). Divergences, all recorded in §6.4:
singleton generator teardown joins the single pass (D-24), sync hooks are called inline (D-21), and
the aggregated errors are returned rather than logged-and-dropped (D-20).

### 5.8 `DestructError` aggregation

`DestructError` is the only error a teardown ever produces, and it is never raised by the teardown
itself — it is *returned*.

| Aspect | Value |
|--------|-------|
| Class | `DestructError(DIError)`, `code="destruct"` (PRD 05 owns the code table) |
| Message | `"{step} on {name} failed during {origin} teardown: {cause}"`, where `step` is `"@pre_destruct"` or `"aclose()"` |
| `detail["class"]` | `name` — the token description (or the instance's type for a multi-binding slot) |
| `detail["stage"]` | `"pre_destruct"` or `"aclose"` |
| `detail["origin"]` | `"scope"` or `"shutdown"` |
| `detail["cause"]` | `str(original_exception)` |
| `__cause__` | The original exception object, for `raise ... from` style inspection |
| Logging | One `ERROR` record on `logging.getLogger("lauren_di")` per failure, with `exc_info=True` |

The reference shapes the same failure almost identically — same keys, same `stage` values, same
`__cause__` — and the one difference is deliberate (§6.4 #10): it builds `origin` from the scope's
*name*. Observed: `"@pre_destruct on Boom failed during scope 's' teardown: boom1"` with
`detail={'class': 'Boom', 'stage': 'pre_destruct', 'origin': "scope 's'", 'cause': 'boom1'}`.
`lauren_di` has no scope name, so `origin` is exactly `"scope"` or `"shutdown"` and the message reads
`"... failed during scope teardown: boom1"`. That is the whole difference: a caller can separate a
scope failure from a shutdown failure by `detail["origin"]` equality rather than by parsing a name.

Where the tuple surfaces:

| Path | How a test reads it |
|------|---------------------|
| `async with container.scope() as scope:` | `scope.errors` after the block |
| `await scope.close()` | the return value |
| `await container.shutdown()` | the return value (D-20) |
| A failed `startup()` rollback | logged only; the original exception is what propagates |

Why collect instead of raise (and why the first error is not re-raised): teardown is a **best-effort
reduction of resources**. Raising the first failure would abandon the remaining instances, turning
one broken `close()` into a leaked connection pool. FastAPI's `yield`-dependency finaliser raises,
and that is a known papercut. Collection also makes the aggregate assertable, which is the only way
"never aborts the remaining teardown" can be tested at all.

Aggregated errors do not suppress the original failure: if the `async with` body raised, that
exception propagates unchanged; teardown errors live in the tuple.

### 5.9 Instance registration and the teardown index

Three members are public so that objects the container did not construct can still participate in the
container's lifetime, and so that teardown can find a provider for a cached instance. Two more —
`set_singleton` and `mark_singleton_initialized` — are internal write-back hooks, not public members,
and `singletons()` is internalised for the same reason (D-53, D-56).

| Member | Signature | Contract |
|--------|-----------|----------|
| `register_instance` | `register_instance(token, instance, *, owning_module=None) -> Provider` | Registers a pre-built object as a singleton **whose lifecycle hooks run**: `@post_construct`/`@pre_destruct` are read from `type(instance)`, with the plain-method fallback in §5.9.1, so `startup()`/`shutdown()` drive them. Registering the same object under a second token does not duplicate the hooks (keyed by `id(instance)`) |
| `adopt_lifecycle` | `adopt_lifecycle(instance) -> None` | Registers `instance` under a hidden internal token so its hooks run, without exposing it to resolution. A no-op when the object declares no hooks or already owns them |
| `instance_index` | `instance_index() -> Mapping[Any, Provider]` | Cache-key → provider, built when the graph is validated and invalidated by registration. This is how the teardown finds `pre_destruct_chain` for a raw cached object — it is public because the teardown contract depends on it, not because it is interesting |

Why `instance_index` is public: the teardown algorithm needs `(cache key) -> Provider` for objects
that carry no back-reference to it (a wrapper, a value, a multi-binding slot keyed by `id(provider)`).
Making it private would make the teardown's correctness unauditable from outside, and PRD 03's
acceptance requires every rule to be testable from the public API alone.

#### 5.9.1 The `startup()` / `aclose()` fallback for externally built objects

An object the container did not construct cannot be assumed to use the decorators — most libraries
ship a `startup()`/`aclose()` pair already. `register_instance` and `adopt_lifecycle` therefore
resolve each direction independently (`instance_lifecycle_hooks` in the reference):

| Situation | Startup hook | Teardown hook |
|-----------|--------------|---------------|
| `@post_construct` / `@pre_destruct` present | the decorated methods, in the §5.5 order | ditto |
| No decorated hook in that direction | the method **actually named** `startup` / `aclose`, if callable | ditto |
| Neither | no hook; the object is constructed-only | no hook |

Rules:

1. **Per direction, not per class.** A class with `@post_construct` and a plain `aclose()` gets the
   decorated startup hook and `aclose()` as its teardown hook. Verified on the reference:
   `instance_lifecycle_hooks(Plain)` with only plain methods yields `(['startup'], ['aclose'])`.
2. **Decorated always wins.** The fallback is consulted only when the decorated chain for that
   direction is empty, so adding decorators never causes a method to run twice.
3. **The name must match exactly.** `_named_hook` requires the attribute's `__name__` to equal
   `"startup"` / `"aclose"`; a partial, a lambda assigned to that name or a differently-named wrapper
   is not a hook. This keeps accidental matches out.
4. **This is the only place the fallback applies.** Providers the container constructs use the
   decorated chain only (`post_construct_chain` / `pre_destruct_chain`); a normal `@injectable` class
   with a method called `startup` gets nothing (PRD 02). The fallback exists because
   `register_instance`/`adopt_lifecycle` are the seam for *foreign* objects.
5. **Not in §5.9's other members.** `set_singleton` marks the instance initialised (no hook fires) and
   `register_value` (PRD 04) tracks no hooks at all; only these two calls adopt lifecycle.

That the fallback is a rule, not an implementation detail, follows from the testability requirement:
T03-55 registers a plain object with `startup`/`aclose`, calls `container.startup()`/`shutdown()` and
asserts both ran — from the public API, no decorators involved.

### 5.10 Container lifecycle: idempotent and restartable

```
        register*                      startup()                shutdown()
PENDING ──────────> CONFIGURED ──────────────> STARTED ────────────────> STOPPED
   ^                    │                         │                        │
   │                    │ resolve() (lazy)        │ resolve()              │ resolve()
   │                    └─────────> WORKS ────────┘                        │  (constructs
   │                                                                       │   lazily;
   └────────────────────────── startup() ──────────────────────────────────┘   hooks fire
                                                                              next startup)
```

Rules (D-22):

1. `startup()` is idempotent: on a started container it does nothing and fires no hook. The
   reference re-fires every hook on a second call; that is the bug this rule replaces.
2. `shutdown()` is idempotent: on a stopped container with an empty cache it returns `()`.
3. `shutdown()` **clears** the singleton cache and the initialised set. After it, resolving the same
   token constructs a fresh instance (new object identity, and its `@post_construct` fires again). The reference keeps the cache,
   so `resolve()` hands back an object whose resources were already released.
4. The container is restartable: `startup()` after `shutdown()` reconstructs every singleton and
   fires `@post_construct` again. This is what makes per-test container reuse safe.
5. Scopes are **not** restartable: a `ResolutionScope` is single use (§5.3 rule 1). Create a new
   one per request; that is the whole point.

### 5.11 A complete lifecycle program

Twenty-seven non-blank lines, runnable, exercising every rule in this PRD through public names only:

```python run
import asyncio
from lauren_di import DIContainer, Scope, injectable, post_construct, pre_destruct

log: list[str] = []

@injectable()
class Pool:
    @post_construct
    async def open(self) -> None: log.append("pool.open")
    @pre_destruct
    async def close(self) -> None: log.append("pool.close")

@injectable(scope=Scope.REQUEST)
class Tx:
    def __init__(self, pool: Pool) -> None: self.pool = pool
    @pre_destruct
    async def rollback(self) -> None: log.append("tx.rollback")

async def main() -> None:
    c = DIContainer(strict_request_scope=True)
    c.register(Tx); c.register(Pool)
    await c.startup()                         # pool.open
    async with c.scope() as scope:
        first = await scope.resolve(Tx)
        assert first is await scope.resolve(Tx)
    assert log == ["pool.open", "tx.rollback"] and scope.errors == ()
    assert await c.shutdown() == ()           # pool.close, then cache cleared
    async with c.scope() as again:            # a new lifetime: nothing was cached
        rebuilt = await again.resolve(Tx)
        assert rebuilt is not first

asyncio.run(main())
```

Three things to notice, because they are the contract: the request scope tore `Tx` down but left
`Pool` alone (it is a singleton); `shutdown()` returned an empty tuple rather than raising; and after
`shutdown()` the container holds nothing, so the next `startup()` starts a new lifetime.
## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

| Reference symbol (file:line) | `lauren_di` home | Change |
|------------------------------|------------------|--------|
| `Scope` (`types.py:40`) | `_scope.py` | Unchanged (three members, `.label`, `__str__`) |
| `_check_scope` (`_di/__init__.py:986`, nested in `compile` at 978) | `_container.py` (private) | Unchanged comparison and `detail` keys; message built through `Scope.label` |
| `compile` scope traversal (`_di/__init__.py:1120–1190`) | `_container.py` | Unchanged: `deps` + `field_deps`, per `list[T]` member, skip `values` tokens and default-satisfied params |
| `_unscoped_request_resolution` (`_di/__init__.py:1541`) | `_container.py` | Unchanged logic; logger name `"lauren_di"`; `_unscoped_warned` stays a per-container set |
| `OperationScope` (`context.py:57`) | `_scope.py` → `ResolutionScope` | **Renamed and trimmed**: no `LaurenApp`, no `name` parameter, no caller-supplied container plumbing; `close()` promoted to the public close path (the reference has only `__aexit__`); `values`, `resolve` kept; `active` → `closed` and `teardown_errors` → `errors` (§6.4 #11) |
| `finalize_scope` (`_di/_scope.py:55`) | `_scope.py` → `tear_down(entries, *, origin)` | **Unified** (D-24): the one algorithm both `ResolutionScope.close()` and `DIContainer.shutdown()` call |
| `_teardown_error` (`_di/_scope.py:111`) | `_scope.py` | Unchanged shape; `origin` values becomes `"scope"` / `"shutdown"` |
| `_instance_label` (`_di/_scope.py:103`) | `_scope.py` | Unchanged (`int` key → instance type) |
| `_GeneratorContextWrapper` (`_di/__init__.py:104`) | `_scope.py` | Moved next to the teardown that calls its `aclose()` |
| `_find_lifecycle_hooks` (`_di/__init__.py:1610`) | `_lifecycle.py` | Unchanged (MRO direction, definition order, dedup, dunder skip) |
| `instance_lifecycle_hooks` (`_di/__init__.py:1648`) + `_named_hook` (1668) | `_lifecycle.py` | Unchanged, **including the plain `startup()` / `aclose()` fallback** (§5.9.1, L-49); used by `register_instance` / `adopt_lifecycle` |
| `LifecycleScheduler.compute_order` (`_lifecycle/__init__.py:36`; class at 29) | `_lifecycle.py` → `topological_order(providers)` | Kept as a **function**; the scheduler class and the `value`-kind pre-sort stay |
| `LifecycleScheduler.run_post_construct` (`_lifecycle/__init__.py:88`) | `DIContainer.startup()` | Idempotent (D-22); no scheduler, no hosted services |
| `LifecycleScheduler.run_pre_destruct` (`_lifecycle/__init__.py:118`) | `DIContainer.shutdown()` | Single pass (D-24), no timeout (D-21), returns `tuple[DestructError, ...]` (D-20) |
| `register_instance` (`_di/__init__.py:691`), `adopt_lifecycle` (729) | `_container.py` | Unchanged, including `id(instance)` hook de-duplication and the §5.9.1 fallback |
| `instance_index` (`_di/__init__.py:1263`) | `_container.py` | Unchanged (built at validation, invalidated by registration) |
| `singletons` / `set_singleton` / `mark_singleton_initialized` (`_di/__init__.py:1563`, 1566, 1570) | `_container.py` | **Internalised** (D-56): same code, but private; `shutdown()` now clears the cache (D-22) |
| `DestructError`, `LifecycleViolationError` (`exceptions.py:710`, 704) | `_errors.py` | Re-based on `DIError`; field shaped unchanged, `detail` gains `stage` / `origin` (§5.8) |
| `DestructTimeoutError` (`exceptions.py:716`) | — | **Dropped** (PRD 01 §7, D-21) |
| `_FRAMEWORK_VALUES` / `register_framework_value` / `framework_value_types` (`_di/_scope.py:32`, 35, 50) | — | **Dropped**: there is no framework to declare types; a scope's `values` mapping replaces the whole mechanism (D-8) |

### 6.2 Lifecycle parity table

Rule IDs are stable and are cited by PRD 05 (errors), 07 (overrides) and 08 (migration). "Observed"
records what `../lauren-framework` actually did on 2026-11-21, probed rather than read from
docstrings.

| Rule | Normative statement | Reference symbol | Observed reference behaviour | Test |
|------|--------------------|------------------|------------------------------|------|
| L-01 | `Scope` values are `TRANSIENT=0`, `REQUEST=1`, `SINGLETON=2` and compare in that order | `Scope` (`types.py:40`) | `TRANSIENT < REQUEST < SINGLETON` is true | T03-01 |
| L-02 | `.label` is the lowercase name and is what messages and `detail` carry | `Scope.label` | `"transient"`, `"request"`, `"singleton"` | T03-02 |
| L-03 | `str(scope)` is the label, not the enum repr | `Scope.__str__` | `str(Scope.REQUEST) == "request"` | T03-03 |
| L-04 | A consumer may not depend on a narrower-scoped provider: reject iff `consumer.scope > dependency.scope` | `_check_scope` | all nine cells behave as §5.2 states | T03-05…T03-13 |
| L-05 | The rejection is `DIScopeViolationError` with `detail={dependent_scope, dependency_scope, dependency}` | `_check_scope` | exactly those three keys | T03-06, T03-08, T03-09 |
| L-06 | Constructor/function parameters are checked | `compile` (`provider.deps`) | singleton function provider taking a request dep raised at `compile()` | T03-10 |
| L-07 | Class-body field deps are checked | `compile` (`provider.field_deps`) | singleton with `d: RequestScoped` raised at `compile()` | T03-11 |
| L-08 | Each `list[T]` member is checked independently | `resolve_one` | per-member `_check_scope` call | T03-12 |
| L-09 | A `values`-seeded token is not an edge and is never checked | `framework_tokens` skip | skipped | T03-13 |
| L-10 | A parameter satisfied by its own default is not an edge | `compile` (`defaults.get(name)`) | `continue` before scope check | T03-13 |
| L-11 | The check runs at validation, therefore at the first `resolve` under D-14 | `compile` | the reference checks only in `compile()`; `resolve()` never checked | T03-06 |
| L-12 | With no active scope, lax mode constructs an untracked instance per resolve and never caches it | `_unscoped_request_resolution` | `resolve(Req) is resolve(Req)` → `False`; nothing is cached | T03-23 |
| L-13 | With no active scope, strict mode raises before constructing | `_unscoped_request_resolution` | `DIScopeViolationError detail={'token': 'Req', 'reason': 'no_active_scope'}` | T03-24 |
| L-14 | Lax mode warns once per token, not once per resolve | `_unscoped_warned` | one record for two resolves | T03-25 |
| L-15 | A `REQUEST` instance is cached per token in the scope's dict and returned again on a second resolve | `_instantiate` request branch | `resolve(A) is resolve(A)` → `True` inside one scope | T03-14 |
| L-16 | A `SINGLETON` resolved through a scope comes from the container, not the scope | `_instantiate` singleton branch | same object across two scopes | T03-15 |
| L-17 | A `TRANSIENT` resolved through a scope is fresh on every resolve | `_instantiate` | two distinct objects | T03-16 |
| L-18 | `resolve()` outside `ACTIVE` raises `LifecycleViolationError` | `OperationScope.resolve` | raised for `PENDING` and for `CLOSED`, `detail={'scope': 'req', 'state': 'pending' \| 'closed'}` | T03-17 |
| L-19 | A scope is single use: a second `__aenter__` raises | `OperationScope.__aenter__` | `LifecycleViolationError`, `detail={'scope': 'req', 'state': 'active'}` (the state at call time, not `closed`) | T03-18 |
| L-20 | `values` is a read-only view of the seeds, and a seeded token resolves to itself | `OperationScope.values` / `resolve` | `MappingProxyType`; seeded token returned as-is | T03-19 |
| L-21 | Leaving the block tears the cache down and clears it | `OperationScope.__aexit__` | cache emptied; `errors` populated | T03-20 |
| L-22 | An exception from the body propagates unchanged after teardown | `OperationScope.__aexit__` | `KeyError` propagated; teardown still ran | T03-21 |
| L-23 | Teardown order is reverse creation order | `finalize_scope` | resolve A, B, gen → `gen.teardown, B.pre, A.pre` | T03-39 |
| L-24 | Per instance, `@pre_destruct` runs before `aclose()` | `finalize_scope` | hook then `aclose` | T03-40 |
| L-25 | A failing step is contained: the rest still runs | `finalize_scope` | two failing hooks both ran, two errors returned | T03-46 |
| L-26 | A contained failure keeps the original as `__cause__` | `_teardown_error` | `RuntimeError` / `ValueError` preserved | T03-47 |
| L-27 | Teardown never raises for hook failures; it returns the aggregate | `finalize_scope` | `list[DestructError]` returned, nothing raised | T03-48 |
| L-28 | Only `Exception` is contained; `BaseException` propagates | `except Exception` | `CancelledError` is not caught | T03-49 |
| L-29 | `@post_construct` hooks run bases first, definition order within a class | `_find_lifecycle_hooks` | `['Base.b1', 'Base.b2', 'Sub.s1']` | T03-26 |
| L-30 | `@pre_destruct` hooks run subclasses first, definition order within a class | `_find_lifecycle_hooks(subclass_first=True)` | `['Sub.t1', 'Sub.t2']` | T03-27 |
| L-31 | An override without the decorator drops the base hook | `_find_lifecycle_hooks` (`owners`) | chain `()` | T03-28 |
| L-32 | An inherited method keeps its hook | `_find_lifecycle_hooks` | subclass chain contains `Base.setup` | T03-29 |
| L-33 | Dunder names are never hooks | `_find_lifecycle_hooks` | `__post_init__` excluded | T03-30 |
| L-34 | One function reachable under two names runs once | `_find_lifecycle_hooks` (`seen`) | single entry | T03-31 |
| L-35 | A non-callable hook target raises `LifecycleConfigError` | `post_construct` / `pre_destruct` | raised | T03-32 |
| L-36 | `REQUEST` and `TRANSIENT` hooks fire at construction | `_instantiate` | `T.post` twice for two transients; `R.post` once per scope | T03-22 |
| L-37 | A singleton's `@post_construct` does **not** fire on `resolve()` | `_instantiate` (singleton branch) | `resolve(S)` → `['S.init']` only | T03-33 |
| L-38 | `startup()` constructs every singleton in dependency order, then fires hooks | `run_post_construct` + `compute_order` | `['Leaf.post', 'Mid.post', 'Top.post']` | T03-34 |
| L-39 | `shutdown()` runs hooks in reverse dependency order | `run_pre_destruct` | `Top.pre, Mid.pre, Leaf.pre` | T03-35 |
| L-40 | `register_instance` runs the hooks of `type(instance)` with the container | `register_instance` | `Ext.post` / `Ext.pre` | T03-36 |
| L-41 | The same object registered under two tokens gets its hooks once | `_lifecycle_owners` | `['Obj.post']` | T03-37 |
| L-42 | `adopt_lifecycle` gives an object hooks without exposing a token | `adopt_lifecycle` | hooks ran for two distinct adopted objects | T03-38 |
| L-43 | `instance_index()` maps cache key → `Provider` and is rebuilt after registration | `instance_index` | `{I1: Provider}` | T03-41 |
| L-44 | `startup()` is idempotent; a second call fires nothing | `run_post_construct` | **diverges**: fired `S.post` twice | T03-51 |
| L-45 | `shutdown()` clears the singleton cache; the container is restartable | `run_pre_destruct` | **diverges**: cache retained, `resolve()` returned the destroyed object | T03-52 |
| L-46 | Sync hooks run inline on the loop; async hooks are awaited | `finalize_scope` vs `run_pre_destruct` | **diverges**: request path inline, singleton path `asyncio.to_thread` + `wait_for` | T03-53 |
| L-47 | Singleton generator teardown participates in the single reverse pass | `run_pre_destruct` (second loop) | **diverges**: second full pass after every hook | T03-54 |
| L-48 | No per-hook teardown timeout exists | `run_pre_destruct(timeout=10.0)` | **diverges**: `DestructTimeoutError` per timed-out hook | T03-50 |
| L-49 | With no decorated hook in a direction, `register_instance`/`adopt_lifecycle` use a method named `startup()` / `aclose()` instead (§5.9.1) | `instance_lifecycle_hooks` / `_named_hook` | `instance_lifecycle_hooks(Plain)` → `(['startup'], ['aclose'])` | T03-55 |

### 6.3 Rejection and containment rules

Every case that raises, every case that is contained, and the cases that are deliberately silent.

| Situation | Outcome | Produced by | `detail` | Test |
|-----------|---------|-------------|----------|------|
| Wider consumer depends on narrower provider | `DIScopeViolationError` | `compile()` / first `resolve()` | `dependent_scope`, `dependency_scope`, `dependency` | T03-06, T03-08, T03-09 |
| `REQUEST` resolved with no scope, strict mode | `DIScopeViolationError` | `resolve()` | `token`, `reason="no_active_scope"` | T03-24 |
| `scope.resolve()` before enter / after close | `LifecycleViolationError` | `ResolutionScope.resolve` | `state` | T03-17 |
| Second `async with` on the same scope | `LifecycleViolationError` | `ResolutionScope.__aenter__` | `state` | T03-18 |
| `@post_construct` / `@pre_destruct` on a non-callable | `LifecycleConfigError` | the decorator, at decoration time | — | T03-32 |
| A `@post_construct` raises during construction | the exception propagates | `resolve()` | — (not wrapped) | T03-42 |
| A `@post_construct` raises during `startup()` | the exception propagates; started singletons are rolled back | `startup()` | — | T03-43 |
| A `@pre_destruct` raises during teardown | **contained**: `DestructError`, remaining hooks and instances still run | `close()` / `shutdown()` | `class`, `stage="pre_destruct"`, `origin`, `cause` | T03-46 |
| `aclose()` raises during teardown | **contained**: `DestructError` with `stage="aclose"` | `close()` / `shutdown()` | as above | T03-44 |
| A hook name is not an attribute of the instance | **contained**: `AttributeError` becomes a `DestructError` | teardown | as above | T03-45 |
| A hook raises `CancelledError` / `KeyboardInterrupt` | **propagates**, aborting the remaining teardown | teardown | — | T03-49 |
| `REQUEST` resolved with no scope, lax mode | **silent except one warning**; untracked instance | `resolve()` | — | T03-23, T03-25 |
| `TRANSIENT` instance outlives the scope | **silent**: never tracked, no teardown | — | — | T03-16 |
| A scope left open at `shutdown()` | **silent**: its cache is untouched | — | — | T03-20 |
| `close()` called twice | **silent**: second call returns the same tuple | `ResolutionScope.close` | — | T03-20 |
| `shutdown()` called twice | **silent**: second call returns `()` | `shutdown()` | — | T03-52 |

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | Graph validation trigger | explicit `compile()` only | first `resolve` validates (PRD 02) | a library cannot require an explicit `compile()`; without it the narrowing rule is never enforced | D-14 |
| 2 | `startup()` idempotence | second call re-fires every `@post_construct` | second call fires nothing | a hook that opens a connection must not open a second one | D-22 |
| 3 | Post-`shutdown()` state | singleton cache retained; `resolve()` returns the destroyed object | cache cleared; next `resolve()` constructs anew; `startup()` restarts | returning a destroyed instance is a correctness bug, not a policy | D-22 |
| 4 | Teardown error reporting | `list[Exception]` logged by the app; `shutdown -> None` | `shutdown()` and `scope.close()` return `tuple[DestructError, ...]`; `scope.errors` | the rule must be assertable from the public API | D-20 |
| 5 | Per-hook timeout | `wait_for(..., timeout=10.0)` + `DestructTimeoutError` | no timeout; the caller wraps the whole call | `DestructTimeoutError` is not carried (PRD 01 §7); a per-hook deadline is policy, not DI | D-21 |
| 6 | Sync-hook threading | singleton hooks via `asyncio.to_thread`; request hooks inline | always inline on the loop | moving user code across threads silently breaks loop-bound hooks; one rule beats two | D-21 |
| 7 | Teardown pass count | singleton generators torn down in a second full pass after all hooks | one reverse pass: per instance, hooks then `aclose()` | "reverse creation order" must mean one order | D-24 |
| 8 | Scope state errors | `LifecycleViolationError` | same class, now an exported leaf; `detail` keeps `state` and drops the reference's `scope` name key | PRD 01 listed it as not carried; a scope object needs it | D-23 |
| 9 | Warn channel for lax mode | `logging.getLogger("lauren")` | `logging.getLogger("lauren_di")` | one logger per distribution | — |
| 10 | `OperationScope.name` | required label used in logs, in the `DestructError` message and in `detail["origin"]` (`"scope 's'"`) | no name: `origin` is exactly `"scope"`/`"shutdown"`, so the message reads `during scope teardown`; all other `DestructError` keys are identical | there is no app to name a scope after, and an `origin` enum can be compared where a name would have to be parsed | — |
| 11 | Scope member spellings | `active`, `teardown_errors` | `closed`, `errors` — same meanings, the first inverted (`closed is not active`) | PRD 01 §5.3.1 fixes `.closed`/`.errors` as the shipped names and PRD 01 §5.4's example asserts them; renaming the class instead would break the public surface for a naming preference | — |

Everything else in §6.2 is parity: same order, same exception class names, same silent cases.

### 6.5 Module layout and import graph

```text
lauren_di/
├── _scope.py       Scope, ResolutionScope, tear_down(), _GeneratorContextWrapper
├── _lifecycle.py   post_construct, pre_destruct, _find_lifecycle_hooks,
│                   instance_lifecycle_hooks, topological_order()
├── _container.py   DIContainer.startup / shutdown / scope / resolve,
│                   _check_scope (called from validation)
├── _provider.py    Provider (PRD 02) — post_construct_chain / pre_destruct_chain
├── _errors.py      DIEror and leaves: DIScopeViolationError, LifecycleViolationError,
│                   LifecycleConfigError, DestructError
└── _introspect.py  (PRD 02) — nothing in this PRD touches it
```

Dependency direction: `_scope.py` imports `_errors.py` and `_provider.py` only; `_container.py`
imports `_scope.py` and `_lifecycle.py`; `_lifecycle.py` imports `_provider.py` only. There is no
cycle, and `_scope.py` is importable without `_container.py` (its teardown takes a plain mapping plus
a provider index, not a container).

### 6.6 Feasibility

- **The algorithms are already written.** `_check_scope` is 15 lines inside `compile`;
  `_find_lifecycle_hooks` is 36; `finalize_scope` plus helpers is ~90; `compute_order` is ~40. The
  work is relocation plus the five divergent behaviours.
- **`startup()`/`shutdown()` are `run_post_construct`/`run_pre_destruct` minus the scheduler.** The
  topological sort is reused verbatim; the initialised-set guard already exists
  (`mark_singleton_initialized`), it just needs to be consulted on entry as well as after each hook.
- **Clearing the cache is the only state change** the restartability rule needs: `shutdown()` already
  walks the cache; adding `self._singletons.clear()` and `self._singletons_initialized.clear()` is
  two lines, and `_instance_index` is unaffected (it maps tokens, not instances).
- **Risk: eager singleton construction at `startup()`.** A singleton nobody uses is still built, so a
  `startup()` can fail on a provider the application never touched. That is the reference's behaviour
  and it is the right one (fail at startup, not in a request), but the docs must say it (§9) because
  it is the most surprising consequence.
- **Risk: no timeout (D-21).** A hook that blocks forever blocks `shutdown()` forever. Mitigation is
  documentation plus the `asyncio.wait_for` workaround; a `shutdown(timeout=...)` keyword can be
  added later without breaking anyone, which is why deferring it is cheap.
- **Risk: `instance_index` invalidation.** The teardown looks providers up through it; a
  registration after compile clears it (PRD 02, D-15) and the next validation rebuilds it. A test
  asserts the rebuilt index finds the provider for a cache entry created before the registration.
- **Cost per request.** One `dict` allocation for the cache, no per-request provider map (the index is
  compiled once), one `reversed()` over the cache at close. Nothing scales with graph size.

## 7. Error Catalog Impact

PRD 05 owns the catalogue (codes, message shapes, `detail` keys, remediation). PRD 03 fixes which
lifecycle errors exist and when they are raised versus contained.

| Change | Detail |
|--------|--------|
| Reused leaf | `DIScopeViolationError` — both the compile-time narrowing rule (`detail` with the two scope labels and the dependency) and the strict no-scope resolution (`detail` with `token`/`reason`) |
| **Re-activated leaf** | `LifecycleViolationError` — a `ResolutionScope` used outside its lifetime (D-23). PRD 01 §7's "not carried" entry is corrected |
| Reused leaf | `LifecycleConfigError` — a lifecycle decorator applied to a non-callable; a generator provider declared `TRANSIENT` (D-17, PRD 02) |
| Reused leaf | `DestructError` — one per contained teardown failure, **returned** not raised |
| Dropped leaf | `DestructTimeoutError` — no per-hook timeout (D-21) |
| Dropped leaf | `DrainTimeoutError` — no connection draining (PRD 01 §7) |
| Signature correction | `DIContainer.shutdown() -> tuple[DestructError, ...]` (was `-> None`) and `ResolutionScope.close() -> tuple[DestructError, ...]`, `ResolutionScope.errors` (D-20); PRD 01 §5.3.3 updated |
| Requirement | No lifecycle error is raised by a teardown. `DestructError` instances are values; `except DestructError` never fires during `close()` or `shutdown()` |

## 8. Phased Implementation Plan

This PRD is phase 2 of PRD 01 §8 (`0.1.x`). It lands in four steps so each is independently testable.

| Step | Contents | Exit criteria |
|------|----------|---------------|
| 2a — Vocabulary and rule | `Scope`; `_check_scope` inside validation; the nine-cell rule; `DIScopeViolationError` details | T03-01…T03-13 green, including the field-dep and `list[T]` edges |
| 2b — Scope object | `ResolutionScope`, `container.scope()`, `values`, `closed`, state guards, cache ownership, strict/lax modes | T03-14…T03-25 green |
| 2c — Hooks | `post_construct`, `pre_destruct`, `_find_lifecycle_hooks`, `instance_lifecycle_hooks`; construction-time firing for request/transient | T03-26…T03-32, T03-22, T03-36…T03-38 green |
| 2d — Singleton lifetime and teardown | `topological_order`, `startup()`, `shutdown()`, the unified `tear_down`, `DestructError` aggregation, restartability, `instance_index`, `singletons`/`set_singleton`/`mark_singleton_initialized` | T03-33…T03-35, T03-39…T03-54 green; §5.11 program runs verbatim |

Step 2a can land with PRD 02's step 1 because the rule lives inside validation. Steps 2b–2d are
independent of PRDs 04–07 and can ship in any order after 2a.

## 9. Documentation & Convention Updates

| Artefact | Update |
|----------|--------|
| `README.md` | One paragraph: three scopes, `container.scope()`, `startup()`/`shutdown()`, and the sentence "shutdown clears the cache; the container is restartable" |
| `docs/guides/scopes.md` | The §5.2 nine-cell table verbatim; the strict/lax table from §5.4; a recommendation to construct with `strict_request_scope=True` |
| `docs/guides/lifecycle.md` | The hook-timing table from §5.5, the MRO ordering rules with the `Base(b1,b2)/Sub(s1)` example, and the `startup()`/`shutdown()` step tables |
| `docs/guides/teardown.md` | The §5.7 algorithm, the `DestructError` shape, the `BaseException` caveat, and the `asyncio.wait_for(container.shutdown(), n)` workaround |
| `docs/reference/scope.md` | `Scope`, `ResolutionScope`, `DIScopeViolationError`, `LifecycleViolationError`, `DestructError` reference entries |
| `docs/migration/from-lauren.md` (PRD 08) | `LifecycleScheduler` → `container.startup()`/`shutdown()`; `app.scope()` → `container.scope()`; `OperationScope` → `ResolutionScope`; `OperationScope.name` gone |
| `llms.txt` / `llms-full.txt` (goal #11) | `Scope`, `ResolutionScope`, `post_construct`, `pre_destruct`, `DestructError`, `DIScopeViolationError`, `LifecycleViolationError` entries; the checker must fail if any is dropped |
| `AGENTS.md` / `CLAUDE.md` (goal #11) | Convention: "lifecycle hooks are collected once at registration and stored on `Provider`; never re-read markers at runtime" and "teardown returns errors, it never raises them" |
| Docstring contract | `Provider.post_construct_chain` / `pre_destruct_chain` docstrings state the ordering rules, because that is where a maintainer looks first |

## 10. Test Plan

Tests live in `tests/unit/lifecycle/` and `tests/parity/`. Everything in §10.1 uses **public names
only** — `lauren_di` imports plus the `Provider` record returned by registration. No test reads
`container._singletons`, `_initialized` or `_unscoped_warned`; where a private map would be
convenient, the assertion uses object identity, `instance_index()`, `scope.errors` or the
return value of `close()`/`shutdown()` instead.

### 10.1 Enumerated test cases

| ID | Test name | Setup | Assertion |
|----|-----------|-------|-----------|
| T03-01 | `test_scope_values_and_order` | import `Scope` | `TRANSIENT == 0 < REQUEST == 1 < SINGLETON == 2`; ordering comparisons hold |
| T03-02 | `test_scope_label` | `Scope.REQUEST.label` | `"request"`; all three labels lowercase |
| T03-03 | `test_scope_str_is_label` | `str(Scope.SINGLETON)`, `repr(Scope.SINGLETON)` | `"singleton"` and `"<Scope.SINGLETON: 2>"` |
| T03-04 | `test_scope_membership_closed` | `list(Scope)` | exactly three members, in declaration order |
| T03-05 | `test_matrix_transient_consumer` | transient consumer × transient/request/singleton dependency | all three register and `compile()` cleanly |
| T03-06 | `test_matrix_request_consumer_transient_dep` | request consumer, transient dep | `compile()` raises `DIScopeViolationError`; `detail == {"dependent_scope": "request", "dependency_scope": "transient", "dependency": ...}` |
| T03-07 | `test_matrix_request_consumer_allowed` | request consumer × request/singleton dep | compiles; resolves inside a scope |
| T03-08 | `test_matrix_singleton_consumer_transient_dep` | singleton consumer, transient dep | `DIScopeViolationError`, `dependent_scope == "singleton"` |
| T03-09 | `test_matrix_singleton_consumer_request_dep` | singleton consumer, request dep | `DIScopeViolationError`, `dependency_scope == "request"` |
| T03-10 | `test_scope_rule_covers_function_param` | singleton `@injectable()` function taking a request dep | `DIScopeViolationError` |
| T03-11 | `test_scope_rule_covers_field_dep` | singleton with `d: RequestScoped` in the class body | `DIScopeViolationError` |
| T03-12 | `test_scope_rule_covers_list_member` | singleton depending on `list[Req]`, one member request-scoped | `DIScopeViolationError` naming that member |
| T03-13 | `test_scope_rule_skips_values_and_defaults` | consumer with `x: Request` defaulted, and a scope-only `values` token | compiles; no error |
| T03-14 | `test_scope_caches_request_instance` | two `scope.resolve(Tx)` in one scope | same object; `Tx` constructed once |
| T03-15 | `test_scope_does_not_cache_singletons` | same singleton through two scopes | same object from the container |
| T03-16 | `test_scope_transient_is_fresh` | two `scope.resolve(Tr)` | different objects; never appears in `errors` after close |
| T03-17 | `test_scope_resolve_requires_active` | `resolve` before enter and after close | both raise `LifecycleViolationError` with `detail["state"]` |
| T03-18 | `test_scope_single_use` | enter, exit, enter again | second enter raises `LifecycleViolationError` |
| T03-19 | `test_scope_values_view` | `container.scope(values={Tok: obj})` | `scope.values[Tok] is obj`; mutating the view raises `TypeError`; `await scope.resolve(Tok) is obj` |
| T03-20 | `test_scope_close_tears_down_and_is_idempotent` | resolve, exit via `close()` twice | first returns the errors tuple, second returns the same tuple; cache emptied (a re-resolve builds a new instance, instances torn down once) |
| T03-21 | `test_scope_body_exception_propagates` | raise inside the block | the same exception propagates; teardown ran; `scope.errors` populated |
| T03-22 | `test_request_and_transient_hooks_fire_at_construction` | request + transient providers with `@post_construct` | fires once per constructed instance, not at `startup()` |
| T03-23 | `test_lax_mode_constructs_untracked` | `DIContainer()` (lax), resolve a request provider twice | different objects; an unrelated singleton is unaffected |
| T03-24 | `test_strict_mode_rejects_untracked` | `DIContainer(strict_request_scope=True)` | `DIScopeViolationError`, `detail["reason"] == "no_active_scope"`, `detail["token"]` names the token |
| T03-25 | `test_lax_warns_once_per_token` | two lax resolves, `caplog` at WARNING on `lauren_di` | exactly one record for that token |
| T03-26 | `test_post_construct_order_bases_first` | `Base(b1, b2)` + `Sub(s1)` | chain is `[Base.b1, Base.b2, Sub.s1]` and fires in that order |
| T03-27 | `test_pre_destruct_order_subclasses_first` | `Base(t1)` + `Sub(t2, t3)` | chain is `[Sub.t2, Sub.t3, Base.t1]` |
| T03-28 | `test_override_without_decorator_drops_hook` | subclass redefines `setup` with no decorator | `pre/post` chain empty |
| T03-29 | `test_inherited_method_keeps_hook` | subclass does not redefine `setup` | chain contains `Base.setup` |
| T03-30 | `test_dunder_never_a_hook` | a marked `__post_init__` | excluded from the chain |
| T03-31 | `test_same_function_under_two_names_runs_once` | alias assignment of a marked method | one chain entry |
| T03-32 | `test_non_callable_hook_target_rejected` | `post_construct(42)`, `pre_destruct("x")` | `LifecycleConfigError` for both |
| T03-33 | `test_singleton_hook_does_not_fire_on_resolve` | resolve a singleton before `startup()` | no `@post_construct` observed; instance exists |
| T03-34 | `test_startup_constructs_in_dependency_order` | `Top(Mid(Leaf))`, register in reverse order | startup order is `Leaf, Mid, Top`; all three exist afterwards |
| T03-35 | `test_shutdown_tears_down_in_reverse_order` | same chain, `shutdown()` | `Top, Mid, Leaf`; returns `()` |
| T03-36 | `test_register_instance_runs_hooks` | `register_instance("EXT", obj)` with hooks on its type | `@post_construct` at `startup()`, `@pre_destruct` at `shutdown()` |
| T03-37 | `test_same_instance_two_tokens_single_hook` | register one object under two tokens | hook fires once |
| T03-38 | `test_adopt_lifecycle_runs_hooks_without_token` | `adopt_lifecycle(obj)` twice with distinct objects | hooks fire for each; `resolve(obj)` raises `MissingProviderError` |
| T03-39 | `test_teardown_reverse_creation_order` | resolve A, B, generator in one scope | teardown order is generator, B, A |
| T03-40 | `test_pre_destruct_before_aclose` | provider whose class hook and generator both record | hook recorded before `aclose` |
| T03-41 | `test_instance_index_is_rebuilt` | compile, `instance_index()`, register a provider, validate again | the new provider is present; a cache entry created before still resolves to its provider |
| T03-42 | `test_post_construct_failure_propagates_on_resolve` | `@post_construct` raises for a request-scoped provider | the exception propagates from `resolve`; nothing cached |
| T03-43 | `test_startup_failure_rolls_back` | two singletons, second's `@post_construct` raises | the original exception propagates; the first is torn down (`@pre_destruct` ran) |
| T03-44 | `test_aclose_failure_contained` | generator provider whose teardown raises | `DestructError` with `stage == "aclose"`; the other instance still torn down |
| T03-45 | `test_missing_hook_attribute_contained` | hook renamed at runtime | `DestructError`; teardown completes |
| T03-46 | `test_teardown_contains_every_failure` | two instances, both `@pre_destruct` raise | both hooks ran; two `DestructError`s in newest-first order; `close()` returns them and does not raise |
| T03-47 | `test_destruct_error_keeps_cause` | one hook raises `ValueError` | `error.__cause__` is that instance; `detail["cause"]` has the message |
| T03-48 | `test_shutdown_returns_aggregate` | singleton whose `@pre_destruct` raises | `await shutdown()` returns `(DestructError,)`; nothing raised |
| T03-49 | `test_base_exception_aborts_teardown` | first (newest) hook raises `asyncio.CancelledError` | it propagates; the older instance's hook did not run |
| T03-50 | `test_no_teardown_timeout` | a hook that sleeps longer than any implied deadline | it completes; no `DestructTimeoutError` exists in `lauren_di` (`import` raises `AttributeError`) |
| T03-51 | `test_startup_is_idempotent` | `startup()` twice | each `@post_construct` recorded exactly once |
| T03-52 | `test_shutdown_clears_and_restarts` | `startup()`, `shutdown()`, `startup()` again; also `shutdown()` twice | after the first shutdown `resolve` constructs a new object (new identity) and hooks fire again; second shutdown returns `()` |
| T03-53 | `test_sync_hooks_run_on_loop_thread` | a sync `@pre_destruct` recording `threading.get_ident()` | the recording thread is the event-loop thread for singleton **and** request teardown |
| T03-54 | `test_singleton_generator_teardown_single_pass` | a singleton generator plus a singleton with `@pre_destruct`, both recording | the generator's teardown runs at its own position in the reverse order, not after all hooks |
| T03-55 | `test_register_instance_plain_startup_aclose` | a plain object with `startup()` and `aclose()` and **no** decorators, passed to `register_instance`; a second object with `@post_construct` plus a plain `aclose()` | `startup` / `aclose` run at `startup()` / `shutdown()`; for the mixed object the decorated hook and `aclose` both run once (§5.9.1, L-49) |

Property-style tests worth adding in the same phase:

- **Matrix totality** — for every `(consumer_scope, dependency_scope)` pair, generate a two-provider
  container and assert `compile()` raises iff `consumer > dependency`, across constructor, function,
  and field edges. This is the nine-cell table turned into a loop, so a future fourth `Scope` member
  cannot slip through.
- **Teardown permutation** — resolve a shuffled set of providers in one scope, assert the teardown
  order is exactly the reverse of the resolution order, for 3 providers' worth of permutations.
- **Aggregation totality** — with `n` failing hooks, assert `len(errors) == n` and that all
  `n` hooks ran, for `n` in 1…5.

### 10.2 Parity harness

`tests/parity/test_prd03_lifecycle.py` imports through the same two-line shim PRD 02 defines
(`tests/parity/_impl.py`), so one test body runs against both implementations:

```python fragment
# tests/parity/test_prd03_lifecycle.py
import os, pytest
from ._impl import DIContainer, Scope, injectable, post_construct, pre_destruct

DIVERGENT = os.environ.get("LAUREN_DI_PARITY_TARGET") == "lauren"
```

Rules L-44…L-48 are the divergent five; each is marked `xfail(strict=True)` against `lauren` with
its decision ID in the reason. Everything else (L-01…L-43) must pass against both. The shim needs one
adapter method because the reference scopes are created through an app:

| `lauren_di` call | Reference equivalent in the shim |
|------------------|----------------------------------|
| `container.scope(values=...)` | `LaurenApp`-free `OperationScope(container, "parity", values=...)` |
| `await container.startup()` | `await LifecycleScheduler(container).run_post_construct()` |
| `await container.shutdown()` | `await LifecycleScheduler(container).run_pre_destruct()` |
| `scope.errors` | `OperationScope.teardown_errors` — the reference's name for the same tuple |

The four adapters live in `_impl.py` only; the test bodies never mention them, which is what keeps
the parity claim honest. PRD 08 owns the full cross-product matrix.

### 10.3 Acceptance traceability for this PRD

| Acceptance clause | Where satisfied |
|-------------------|-----------------|
| `Scope` ordering `TRANSIENT < REQUEST < SINGLETON` | §5.1, L-01…L-03, T03-01…T03-04 |
| The compile-time narrowing rule | §5.2, L-04…L-11, T03-05…T03-13 |
| **A state table covers all six consumer/dependency scope pairs with allowed/error outcome** | §5.2 nine-cell table — the six off-diagonal pairs are exactly the three allowed widenings (T→R, T→S, R→S: T03-05, T03-07) and the three rejections (R→T, S→T, S→R: T03-06, T03-08, T03-09) |
| Strict vs lax request-scope resolution | §5.4, L-12…L-14, T03-23…T03-25 |
| The explicit scope context manager that owns the request cache | §5.3, L-15…L-22, T03-14…T03-21 |
| `@post_construct` / `@pre_destruct` MRO ordering (bases first on startup, subclasses first on teardown) | §5.5, L-29…L-35, T03-26…T03-32 |
| Who fires singleton hooks without a `LifecycleScheduler` | §5.6, L-37…L-39, T03-33…T03-35 |
| **Reverse-creation-order teardown, specified step by step** | §5.7 five-step algorithm, L-23…L-24, T03-39…T03-41 |
| **`DestructError` aggregation that never aborts the remaining teardown** | §5.8, L-25…L-28, T03-46…T03-49 |
| Every lifecycle rule testable from the public API alone | §10 preamble; §10.1 uses only `lauren_di` names plus identity / `instance_index()` / `errors` / return values |
| Externally built objects participate in the lifecycle, with the reference's plain-method fallback | §5.9.1, L-40…L-42, L-49; T03-36…T03-38, T03-55 |
