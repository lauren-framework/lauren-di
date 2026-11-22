# PRD 07 — Testing Overrides and Isolation

| | |
|---|---|
| **Status** | Draft — 2026-11-21 |
| **Depends on** | 02 (`Provider`, registration entry points), 03 (scope cache, hooks, teardown), 04 (multi-bindings, aliases, `CustomProvider`), 05 (`compile()` phases), 06 (visibility domains) |
| **Introduces** | D-44 … D-49 |
| **Rules** | OV-01 … OV-34 |
| **Tests** | T07-01 … T07-34 |
| **Probes** | `E-301` … `E-325` against `../lauren-framework` on Python 3.13; indexed in §6.6 |

---

## 1. Problem Statement

A test needs to replace one provider without touching the graph around it. The
container is the only thing that knows how rows are built, cached, scoped and
torn down, so the replacement has to be a container feature — a test that
monkeypatches `DIContainer._providers` is asserting against a private layout and
will break on the first refactor.

The reference already ships this feature, and the shape it chose is welded to the
app factory: `OverridePlan` / `ProviderOverride` live in `_di/_overrides.py`,
`LaurenFactory.create` reads the plan that is active in the current context
(`_asgi/__init__.py:3944`), hands it to the `DIContainer`, and the plan is
activated by `lauren.testing.TestingModuleBuilder.compile()` inside
`building_with` (`testing/_module.py:392`). A DI-only library has no factory and
no module graph, so the decision is not "copy it" but "which part of it is the
DI concern, and where does the rest go".

### 1.1 What is genuinely DI, and what is framework

| Piece | DI concern? | Why |
|-------|-------------|-----|
| `ProviderOverride` payload (five kinds) | **Yes** | It describes a replacement row; nothing about HTTP |
| `OverridePlan` keyed by token | **Yes** | One plan per container build |
| Replacing a row at registration time | **Yes** | Keeps visibility, `multi`, `provides`, scope, hooks intact |
| Target-missing validation | **Yes** | It is a wiring error, so it belongs to `compile()` |
| `building_with` / `active_override_plan` (ContextVar) | **Yes**, but with a caveat | The container has no `create()` to wrap; see §5.5 |
| `OverridePlan.modules` (module substitution) | **No** | Needs the declarative graph, which D-37 keeps out of scope |
| `override_guard` / `override_interceptor` / `override_middleware` / `override_exception_handler` | **No** | HTTP roles; PRD 01 §3 |
| `TestingModuleBuilder` chain API | **Partly** | Its `use_*` vocabulary is worth keeping; its module plumbing is not |

### 1.2 Defect: `value` and `existing` silently change a provider's scope

`_register_override` builds value rows with `scope=Scope.SINGLETON` hardcoded
(`_di/__init__.py:526–548`) and alias rows the same way (`:614–626`). The
target's scope is used only for `class` and `factory`
(`scope=override.scope if override.scope is not None else target_scope`, `:513`).
Measured:

- `E-304`: overriding a `Scope.REQUEST` row reports `singleton` for `value` and
  `existing`, `request` for `class` and `factory`.
- `E-302`: the same `REQUEST` row resolved inside two *different* request caches
  returns the identical object (`E-302c -> True`), so a value double is shared
  across what the container calls two requests.

A test double that outlives its request is exactly the kind of state leak tests
exist to catch, and the container reports the wrong scope while doing it.

### 1.3 Defect: an override can hide a scope violation

Scope narrowing is enforced on edges during `compile()` (`:986–1007`, `:1095`).
The *row's* scope is what gets compared, so replacing a `REQUEST` row with a
value removes the violation from the graph:

```text
E-303  singleton consumer -> REQUEST dependency, no override
       DIScopeViolationError {'dependent_scope': 'singleton',
                              'dependency_scope': 'request',
                              'dependency': 'RequestRepo'}
E-303b same graph, REQUEST row value-overridden -> compile() ok
E-303c consumer built with the fake -> 'FAKE'
```

Production startup fails; the test suite passes. That is worse than not
supporting the override at all, because the green build is a lie.

### 1.4 Defect: `use_values` on a non-`multi` token flips the row to `multi`

`_register_override` builds `values` rows with `multi=True` unconditionally
(`:481–486`), ignoring the target's flag. Measured (`E-305`): overriding a plain
`Repo` registration with `use_values(["A", "B"])` makes `resolve(Repo)` return
`['A', 'B']` and `get_provider(Repo).multi` report `True`. A token whose
resolution type used to be `Repo` now yields a `list` — the exact class of
silent semantic change PRD 05 exists to prevent, and no compile phase sees it.

### 1.5 Defect: a bad payload fails at the wrong time, and leaves a half row

`use_class` is validated inside the container's registration path (`:560–566`),
so `OverridePlan({Repo: ProviderOverride(kind="class", use_class="nope")})`
constructs fine, and the error surfaces only when some registration happens to
hit the overridden token:

```text
E-316  register(Repo) -> UnresolvableParameterError: use_class override for Repo needs a class
E-316b compile() afterwards -> ok          # the original row is installed, the override is not
```

Two problems in one: the failure depends on registration order (a plan that
never matches a token never validates its own payload), and the container is left
in a state where the override is silently absent instead of the build failing.

### 1.6 What is actually being decided

Not *whether* overrides ship — every user of a DI container needs them — but:

1. **Where the plan lives.** A constructor argument on `DIContainer`, per PRD 02's
   "the container is the unit of composition", or a process-global registry.
2. **Whether the ContextVar survives.** It exists in the reference to wrap
   `LaurenFactory.create`. With no factory, it is either a deliberate, narrow
   seam for *builders* or dead weight.
3. **What an override is allowed to change.** Scope, `multi`, `provides`,
   visibility, lifecycle hooks — each is a decision, and the reference got two of
   them wrong (§1.2, §1.4).
4. **What isolation means, precisely.** "Tests do not leak into each other" is
   not testable until it is written as statements about threads, tasks, nesting,
   exceptions and containers.

---

## 2. Goals

1. One plan per container build: `DIContainer(overrides=plan)`, read at
   registration time, with no process-global state involved in resolution.
2. Five override kinds with the reference's vocabulary — `value`, `values`,
   `class`, `factory`, `existing` — and a payload that is validated when the plan
   is built, not when a token happens to be registered.
3. The override keeps everything about the target that is not the replacement
   itself: scope (per D-46), `multi` flag, `provides` bindings, `owning_module`
   visibility domain, and lifecycle hooks of the replacement class.
4. An override may not weaken validation: an edge that violates scope narrowing
   still fails `compile()` after an override is applied (D-48).
5. `optional=True` overrides that match nothing are ignored; non-optional ones
   that match nothing fail `compile()` with `detail["reason"] == "target_missing"`.
6. A written concurrency guarantee (§5.6) with one test per clause.
7. A pytest pattern that gives every test a fresh container, and a documented
   reason why sharing one is wrong.

---

## 3. Non-Goals

- **Module substitution.** `OverridePlan.modules` and `override_module(A).with_module(B)`
  need the declarative graph. D-37 puts it out of scope for v1 and v2; a test that
  wants a different module registers different providers.
- **HTTP roles.** Guards, interceptors, middlewares, exception handlers and
  `Test.create_testing_module()` stay in the framework. `ProviderOverride` ships
  without the reference's `role` field, because the only role a DI-only container
  has is "provider".
- **A test client.** No `TestClient`, no request plumbing, no fixtures that speak
  HTTP. PRD 01 §3.
- **Monkeypatching helpers.** No `patch()` / `mock()` integration. A value
  override *is* the mechanism; tests that need call assertions pass a spy object
  as the value.
- **Automatic restoration.** The plan is not "un-applied" after a container
  exists; the container owns its rows for life (D-49). Isolation comes from
  building a new container, not from undoing an override.

---

## 4. Prior Art

| System | Mechanism | What we take | What we leave |
|--------|-----------|--------------|---------------|
| **NestJS** | `Test.createTestingModule({imports}).overrideProvider(T).useValue(v).compile()` | The five verbs, the builder chain, overriding at module-compile time rather than at injection time | The `TestingModule` (a second graph builder) and `overrideGuard/Interceptor/Pipe` |
| **Spring** | `@MockBean` replaces a bean in the test context; the context is per test class | Fresh context per test; replacement installed *before* the graph is validated | The global bean registry and `@DirtiesContext` gymnastics |
| **pytest fixtures** | `@pytest.fixture` with function scope; `conftest.py` for shared builders | The per-test container fixture pattern (§5.9) | Fixture-scoped containers: a `session`-scoped container shares singletons and their state |
| **dependency-injector** | `container.override(provider).with_(...)` on an existing container, restorable | Nothing — it mutates a live container, which is the failure mode §5.6 forbids | Everything |
| **lauren (reference)** | `OverridePlan` + ContextVar + `building_with` | The data model, `applied_overrides()`, target verification | Scope handling (§1.2), `values` flag handling (§1.4), payload validation timing (§1.5), `modules` |

---

## 5. Proposed API

### 5.1 Data model

```python fragment
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class ProviderOverride:
    """Replacement for every registration of ``token`` in one container build."""

    token: object
    kind: str                      # "value" | "values" | "class" | "factory" | "existing"
    value: object = None
    values: tuple[object, ...] = ()
    use_class: type | None = None
    factory: Callable[..., object] | None = None
    injects: tuple[object, ...] = ()
    existing: object = None
    scope: Scope | None = None     # None -> inherit the target's scope
    optional: bool = False


@dataclass(frozen=True, slots=True)
class OverridePlan:
    """One immutable plan per container build."""

    providers: Mapping[object, ProviderOverride] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        # OV-02: copy once, then freeze — a caller cannot mutate a live plan.
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))
```

- **OV-01.** `ProviderOverride` and `OverridePlan` are frozen. The reference
  documents `OverridePlan` as immutable but stores mutable `dict`s
  (`_overrides.py:58–69`), so `plan.providers[T] = other` succeeds and a late
  registration can observe the mutation. `E-319` shows the reference's
  already-installed rows surviving the mutation (`('V1', 'V1')`), which proves the
  read happens once — but "safe by accident" is not a contract. lauren-di freezes
  the mapping at construction (T07-01).
- **OV-02.** `OverridePlan.providers` is copied into an immutable mapping at
  construction; two containers built from one plan share no mutable state
  (`E-320` measures the reference doing this correctly for installed rows; the
  freeze makes it true for every future path) (T07-02).
- **OV-03.** `ProviderOverride.role` is not part of the type. The reference
  carries it for guards and handlers (`testing/_module.py:104`), which are
  non-goals here (T07-03).
- **OV-04.** `OverridePlan.modules` is not part of the type (D-44) (T07-04).

### 5.2 The five kinds

Each kind lowers to an ordinary `Provider` row, with the replaced row's token,
`owning_module`, `provides` and `multi` flag, and the effective scope of §5.3.
Nothing about the container's resolution path is special-cased for overrides —
`get_provider(T)` returns the replacement row, and PRDs 02–06 apply unchanged.

```python fragment
from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable


@injectable()
class Clock:
    def now(self) -> str:
        return "real"


class FrozenClock:
    def now(self) -> str:
        return "2026-11-21T00:00:00Z"


plan = OverridePlan(
    {Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())}
)
container = DIContainer(overrides=plan)
container.register(Clock)
container.compile()

assert (await container.resolve(Clock)).now() == "2026-11-21T00:00:00Z"
```

```python fragment
# kind="values" - replace a whole multi-bound collection.
# The target must be registered with multi=True; a single row is rejected at
# plan construction with non_multi_values (OV-06), never silently promoted.
plan = OverridePlan(
    {
        Repo: ProviderOverride(
            token=Repo, kind="values", values=(FakeRepo("A"), FakeRepo("B"))
        )
    }
)
container = DIContainer(overrides=plan)
container.register(Repo, multi=True)
container.compile()

assert [repo.name for repo in await container.resolve(list[Repo])] == ["A", "B"]
```

```python fragment
# kind="class" - the double needs dependencies of its own, so build it through DI.
class FakeRepo:
    def __init__(self, clock: Clock) -> None:   # resolved like any registered class
        self.clock = clock


# scope= may narrow a SINGLETON target to REQUEST, never the reverse (OV-12).
plan = OverridePlan(
    {
        Repo: ProviderOverride(
            token=Repo, kind="class", use_class=FakeRepo, scope=Scope.REQUEST
        )
    }
)
container = DIContainer(overrides=plan)
container.register(Clock)
container.register(Repo)                     # SINGLETON by default
container.compile()

assert isinstance(await container.resolve(Repo), FakeRepo)
```

```python fragment
# kind="factory" - positional injects; an OptionalDep lowers to None (OV-08).
def make_fake(clock: Clock, cfg: Config | None) -> FakeRepo:
    return FakeRepo(clock, cfg)


plan = OverridePlan(
    {
        Repo: ProviderOverride(
            token=Repo,
            kind="factory",
            factory=make_fake,
            injects=(Clock, OptionalDep(Config)),
        )
    }
)
container = DIContainer(overrides=plan)
container.register(Clock)
container.register(Repo)
container.compile()

assert isinstance(await container.resolve(Repo), FakeRepo)
```

```python fragment
# kind="existing" - an alias row: resolution forwards to the named token inside
# that row's own visibility domain and scope, so it cannot tunnel past PRD 06's
# filter or PRD 03's scope rule (OV-09).
class SqlRepo:
    """The real implementation, registered under its own token."""


container = DIContainer(
    overrides=OverridePlan(
        {Repo: ProviderOverride(token=Repo, kind="existing", existing=SqlRepo)}
    )
)
container.register(SqlRepo)
container.register(Repo)
container.compile()

assert isinstance(await container.resolve(Repo), SqlRepo)
```

| Kind | Builder verb | Payload | Row it builds | Use when |
|------|--------------|---------|---------------|----------|
| `value` | `use_value(obj)` | `value` | Singleton-style value row (cached; see §5.3) | A ready-made double or spy |
| `values` | `use_values(iterable)` | `values` (≥1) | One row per value, all `multi=True` | Replacing a whole `list[T]` collection |
| `class` | `use_class(C)` | `use_class` (+ optional `scope`) | Class row built through DI | A double that needs its own dependencies |
| `factory` | `use_factory(fn, injects=(...))` | `factory`, `injects` | Function row, positional injects | A double whose construction is a lambda |
| `existing` | `use_existing(token)` | `existing` | Alias row forwarding to `token` | Pointing a token at another registration |

- **OV-05.** Every kind preserves the target row's `multi` flag and `provides`
  tuple, so `list[T]` membership and Protocol bindings survive the override. The
  reference does this correctly: `E-314c` resolves `list[Repo]` to `['FAKE']` for
  a `multi=True, provides=[Repo]` row that was value-overridden (T07-05).
- **OV-06.** `values` requires a `multi=True` target. On a single-row token the
  plan is rejected with `OverrideError(detail["reason"] == "non_multi_values")`
  (D-47). The reference flips the row to `multi=True` and changes `resolve(T)`
  from `T` to `list[T]` (`E-305`) — a silent type change no compile phase catches
  (T07-06).
- **OV-07.** `use_class(C)` builds `C` through DI exactly like a registered class:
  constructor parameters and class-body fields are resolved, and `C`'s own
  `@post_construct` / `@pre_destruct` chains apply (`E-315b`, `E-315c`; the hooks
  follow PRD 03's scope split, measured in `E-322`/`E-323` for `REQUEST` and
  `E-324`/`E-325` for `SINGLETON`) (T07-07).
- **OV-08.** `use_factory(fn, injects=(A, B))` resolves each inject like a
  provider dependency and calls `fn(a, b)` positionally. An `OptionalDep` in
  `injects` lowers to `None` when missing, matching PRD 04 §5.4. `E-317` measures
  the reference passing the resolved `Config` as the first positional argument
  (`E-317b`, `E-317c` are the value and the injected element) (T07-08).
- **OV-09.** `use_existing(token)` installs an alias row; resolution forwards to
  `token` **in the alias row's own visibility domain and scope**, so an alias can
  never tunnel around PRD 06's filter or PRD 03's scope rule. `E-313` measures the
  reference resolving the alias target correctly while reporting `SINGLETON` for
  an alias of a `REQUEST` row — the target resolution is right, the scope
  attribute is wrong (T07-09).
- **OV-10.** An override replaces every registration of its token: the direct
  one, each `provides` binding, and each later (lazy) registration. The reference
  applies the same rule (`_bind_token`, `:438–455`) and `E-318` shows the
  consequence at token level — `resolve(Sender)` returns the fake while
  `resolve(Smtp)` still returns the real class, because the override targets the
  protocol token, not the implementation (T07-10).

### 5.3 Scope inheritance

The override inherits the target's scope unless `scope=` is given. That is one
rule, applied to all five kinds:

| Target scope | `value` | `values` | `class` / `factory` | `existing` |
|--------------|---------|----------|---------------------|------------|
| `SINGLETON` | `SINGLETON` | `SINGLETON` | `SINGLETON` (overridable) | `SINGLETON` |
| `REQUEST` | `REQUEST` | `REQUEST` | `REQUEST` (overridable) | `REQUEST` |
| `TRANSIENT` | `TRANSIENT` | `TRANSIENT` | `TRANSIENT` (overridable) | `TRANSIENT` |

- **OV-11.** A `REQUEST`-scoped target stays `REQUEST`-scoped when value- or
  alias-overridden (D-46). The row is cached in the request cache, so two request
  caches see two entries — the opposite of `E-302c`, where one object served both
  (T07-11).
- **OV-12.** `scope=` may only narrow, never widen: `use_class(C, scope=Scope.REQUEST)`
  on a `SINGLETON` target raises `OverrideError(detail["reason"] == "scope_widen")`
  at plan construction. Widening would let a replacement outlive the lifetime its
  consumers were validated against (T07-12).
- **OV-13.** `get_provider(T).scope` reports the effective scope, not the target's
  and not a hardcoded `SINGLETON`. `E-304` measures the reference reporting
  `singleton` for `value`/`existing` overrides of a `request` row (T07-13).
- **OV-14.** A `value` override of a `TRANSIENT` target returns the same object on
  every resolution (a value cannot be rebuilt), but the row still reports
  `TRANSIENT` and is not cached — the documented asymmetry, asserted rather than
  implied (T07-14).

### 5.4 Target validation and `optional`

```python fragment
plan = OverridePlan(
    {
        Metrics: ProviderOverride(token=Metrics, kind="value", value=NoopMetrics()),
    }
)

container = DIContainer(overrides=plan)
container.register(Repo)          # nothing registers Metrics
container.compile()               # OverrideError: reason="target_missing"
```

- **OV-15.** A non-optional override whose token nothing registers fails
  `compile()` with `OverrideError` carrying `token`, `kind`, `reason="target_missing"`
  and a hint naming `.optional()` (D-45). The reference performs this check after
  the whole app is built, in the testing builder (`_verify_targets`,
  `testing/_module.py:397–412`, `detail["reason"] == "override_target_missing"`);
  lauren-di moves it into `compile()` so a bad plan fails before any instance
  exists (T07-15).
- **OV-16.** An `optional=True` override that matches nothing is ignored: no
  error, no row, and it does not appear in `applied_overrides()`. The container
  itself never consults `optional` (`E-306` shows the reference's container
  applying nothing and reporting an empty set) — the flag is consumed by
  validation only (T07-16).
- **OV-17.** Payload shape is validated when the `ProviderOverride` is
  constructed: exactly the fields a kind requires, `kind` in the five, `values`
  non-empty, `use_class` a class, `factory` callable, `injects` a tuple of
  hashable tokens (T07-17).
- **OV-18.** A payload error is an `OverrideError`, not a registration-time
  surprise in an unrelated call. `E-316` measures the reference raising
  `UnresolvableParameterError` from `register(Repo)` and then compiling cleanly
  with the override silently absent (`E-316b`) (T07-18).
- **OV-19.** Overriding the same token twice in one plan is impossible by
  construction (a mapping) and detected by the builder chain with a message
  naming the token, mirroring the reference's immediate
  `DecoratorUsageError` (`testing/_module.py:360–367`) (T07-19).

### 5.5 Activation and the ContextVar

```python fragment
from lauren_di import active_override_plan, building_with

with building_with(plan):
    container = make_container()      # builds a DIContainer, with no plan argument
# outside the block the plan is no longer visible
assert active_override_plan() is None
```

- **OV-20.** `DIContainer(overrides=plan)` is the primary, explicit path. When
  `overrides` is omitted the constructor falls back to `active_override_plan()`,
  so a build performed inside `building_with` picks the plan up without the
  helper that builds it knowing overrides exist (T07-20).
- **OV-21.** `building_with(plan)` / `active_override_plan()` are exported for
  that reason: they are the seam a *test helper* uses, not something the
  container needs. The alternative — ambient state consulted during resolution —
  would put a ContextVar read on the hot path; here the plan is read once, at
  construction, and then owned by the container. An explicit `overrides=`
  argument always wins over the ambient plan, so a helper can deliberately
  replace a caller's plan (T07-21).
- **OV-22.** `building_with` resets the variable in a `finally`, so an exception
  inside the block leaves no active plan: `E-310` measures `None` afterwards
  (T07-22).
- **OV-23.** Nesting is well-defined: the inner plan is active inside the inner
  block and the outer plan is restored on exit. `E-309` measures
  `('OUTER', 'INNER', 'OUTER')` (T07-23).
- **OV-24.** After construction the container never consults the ContextVar
  again. It stores the plan it resolved — argument or ambient — and applies it
  when rows are installed, so activating a different plan later cannot
  retroactively change that container. The reference behaves the same way once a
  row is installed (`E-319`: `('V1', 'V1')`), and freezing the plan (OV-01)
  removes the remaining alias (T07-24).

### 5.6 The isolation guarantee

Four statements, each with a test that fails if it stops being true:

> **CG-1 — No cross-container leakage.** Overrides applied to one container are
> never observable from another container, even when both were built from the same
> `OverridePlan` object and even when they resolve the same token. (`E-320`:
> `('SHARED', 'SHARED')` — each container has its own rows; T07-25.)
>
> **CG-2 — No cross-thread leakage.** A plan activated in one thread is invisible
> to another thread. Two threads building with different plans for the same token
> each resolve their own override. (`E-307`: `{0: ('FAKE0', ...), 1: ('FAKE1', ...)}`;
> the main thread's plan is `None` afterwards, `E-307b`; T07-26.)
>
> **CG-3 — No cross-task leakage.** A plan activated inside one `asyncio` task is
> invisible to a sibling task; `contextvars` copies the context at task creation,
> and a plan set *inside* a task does not propagate back out. Two tasks that
> interleave their builds each resolve their own override. (`E-308`:
> `['TASK-A', 'TASK-B']`; T07-27.)
>
> **CG-4 — No leakage past the block.** After `building_with` exits — normally or
> by exception — `active_override_plan()` is `None` (or the enclosing plan), and
> no container built later can pick it up. (`E-310`, `E-309`; T07-28.)

Two things the guarantee deliberately does **not** claim, because they would be
false: a container built *inside* a block keeps its overrides after the block
(they are its rows, not a borrowed context), and a plan object that a caller
mutates through a private attribute is the caller's problem — which is why the
plan is frozen (OV-01).

### 5.7 Resolution matrix

For a token `T` with one non-`multi` registration, overridden by each kind:

| Kind | `resolve(T)` | `resolve(list[T])` | `get_provider(T).scope` | `compile()` validation |
|------|--------------|--------------------|--------------------------|------------------------|
| `value` | the value | `OverrideError` (`non_multi_values` only applies to the *plan*; `list[T]` needs a multi row, PRD 04 §5.8) | target's scope | target must exist unless `optional` |
| `values` | `OverrideError(non_multi_values)` at plan time | n/a — the plan never builds | n/a | rejected before the container exists |
| `class` | instance of `C` | same rule as the target's `multi` flag | `override.scope` or target's | `C` must be a class; its deps must resolve |
| `factory` | `fn(*resolved)` | same rule as the target's `multi` flag | `override.scope` or target's | `injects` must resolve |
| `existing` | the target token's value | same rule as the target's `multi` flag | target's scope | alias target must exist (PRD 04 §5.5) |

- **OV-25.** `applied_overrides()` returns the tokens whose rows an override
  replaced, and nothing else: `frozenset({T})` after a successful match
  (`E-301c`), `frozenset()` for an unmatched optional override (`E-306b`), and it
  is a pure read (T07-25).
- **OV-26.** An override applies to a row installed *after* `compile()` too: the
  container keeps the plan for its lifetime and consult it on every registration,
  so a lazily registered token is still replaced. `E-311` measures the reference
  doing exactly this (`'LATE'` after `compile()` then `register`) (T07-26).

### 5.8 Interactions

- **OV-27.** **Visibility.** The override row keeps the target's `owning_module`,
  so it is visible in exactly the domains the target was. `E-312` measures a row
  registered in `ModA` staying invisible from `ModB` *and* from `ModA` when
  `set_visible(ModA, ∅)` hides its own rows (the PRD 06 §1.5 defect), and never
  becoming global. An override is not an escape hatch from encapsulation (T07-29).
- **OV-28.** **Lifecycle.** A `value` override never runs hooks — there is no user
  construction to hook (`testing/_module.py:107`). A `class`/`factory` override
  runs the replacement's own chains under PRD 03's rules; a `SINGLETON`
  replacement's `@post_construct` therefore fires at container startup, not at
  first resolve, exactly like a normal singleton (`E-324`, `E-325`) (T07-30).
- **OV-29.** **Teardown.** A `class`/`factory` override's instance joins the
  reverse-creation-order teardown of PRD 03 §5.6 like any other row; a `value`
  override's object is never torn down by the container, because the container
  did not create it (T07-29).
- **OV-30.** **Diagnostics.** `explain(T)` (PRD 06 §5.8) reports the override row
  and adds `override=<kind>` to its verdict, so a surprising resolution is
  attributable without reading the plan (T07-30).

### 5.9 The pytest pattern

A fresh container per test is the only pattern that gives CG-1 for free: the
singleton cache, the request cache, the visibility grants and the teardown list
all die with the container.

```python fragment
# conftest.py
import pytest
from lauren_di import DIContainer


@pytest.fixture
async def container():
    c = DIContainer()
    c.register(Clock)
    c.register(Repo)
    c.register(Service)
    c.compile()
    yield c
    # PRD 03 §5.6: teardown is explicit; nothing fires on garbage collection.
    await c.shutdown()
```

```python fragment
# test_service.py
from lauren_di import OverridePlan, ProviderOverride


async def test_service_uses_the_frozen_clock(container):
    plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())})
    overridden = DIContainer(overrides=plan)
    for target in (Clock, Repo, Service):
        overridden.register(target)
    overridden.compile()
    ...
```

- **OV-31.** The documented pattern builds a *new* container for an overridden
  test rather than mutating the fixture's container; the fixture stays
  override-free so a test that forgets to apply its plan fails loudly instead of
  silently inheriting one (T07-31).
- **OV-32.** A `session`-scoped container is explicitly discouraged: singletons
  persist across tests, and a `class` override's hooks run once for the whole
  session. The docs state the failure mode, not just the recommendation (T07-32).

---

## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

| Reference | lauren-di | Change |
|-----------|-----------|--------|
| `_di/_overrides.py:ProviderOverride` | same name, same five kinds | drop `role`; validate payload in `__post_init__` |
| `_di/_overrides.py:OverridePlan` | same name | drop `modules`; freeze `providers` |
| `_di/_overrides.py:OVERRIDE_KINDS` | same name | unchanged |
| `_di/_overrides.py:building_with` / `active_override_plan` | same names | unchanged semantics |
| `DIContainer(overrides=...)` | same keyword | unchanged |
| `DIContainer.applied_overrides()` | same method | unchanged |
| `_register_override` / `_override_value_row` / `_override_built_row` | same internal shape | scope from the target for *all* kinds; `values` requires `multi` |
| `TestingModuleBuilder` chain (`override_provider(T).use_value(...)`) | **not carried** | a dict literal in `OverridePlan` is the API; the builder's chain is sugar over a module graph we do not have |
| `_verify_targets` | folded into `compile()` | `detail["reason"]` becomes `target_missing` |
| `OverridePlan.modules` / `override_module` | **not carried** | non-goal §3 |
| `override_guard` / `_interceptor` / `_middleware` / `_exception_handler` | **not carried** | framework roles |

### 6.2 Parity table — 34 rules, 34 tests

| Rule | Statement | Test |
|------|-----------|------|
| OV-01 | Plan and override are frozen | T07-01 |
| OV-02 | Plans share no mutable state between containers | T07-02 |
| OV-03 | No `role` field | T07-03 |
| OV-04 | No `modules` field | T07-04 |
| OV-05 | `multi` and `provides` survive the override | T07-05 |
| OV-06 | `values` on a non-`multi` token is an error | T07-06 |
| OV-07 | `use_class` is DI-built and runs its own hooks | T07-07 |
| OV-08 | `use_factory` injects resolve positionally, `OptionalDep` → `None` | T07-08 |
| OV-09 | `use_existing` resolves in the alias's domain and scope | T07-09 |
| OV-10 | An override replaces every registration of its token | T07-10 |
| OV-11 | Scope is inherited by every kind | T07-11 |
| OV-12 | `scope=` may narrow, never widen | T07-12 |
| OV-13 | `get_provider` reports the effective scope | T07-13 |
| OV-14 | `value` on `TRANSIENT` is stable but uncached | T07-14 |
| OV-15 | Unmatched non-optional override fails `compile()` | T07-15 |
| OV-16 | Unmatched optional override is ignored | T07-16 |
| OV-17 | Payload validated at plan construction | T07-17 |
| OV-18 | Payload error is `OverrideError`, never registration-order dependent | T07-18 |
| OV-19 | Duplicate override is rejected with the token named | T07-19 |
| OV-20 | `DIContainer(overrides=)` needs no ContextVar | T07-20 |
| OV-21 | `building_with` activates for builders | T07-21 |
| OV-22 | The variable resets on exception | T07-22 |
| OV-23 | Nesting restores the outer plan | T07-23 |
| OV-24 | The container never re-reads the ContextVar | T07-24 |
| OV-25 | `applied_overrides()` is exact and read-only | T07-25 |
| OV-26 | Late registrations are still overridden | T07-26 |
| OV-27 | Visibility domain is preserved | T07-27 |
| OV-28 | Hooks follow PRD 03's scope split | T07-28 |
| OV-29 | Teardown covers built rows, never values | T07-29 |
| OV-30 | `explain()` attributes the override | T07-30 |
| OV-31 | The fixture pattern builds a new container for overridden tests | T07-31 |
| OV-32 | A `session`-scoped container is documented as unsafe | T07-32 |
| OV-33 | Every `OverrideError` names token and kind | T07-33 |
| OV-34 | `OverrideError` precedes any instance construction | T07-34 |

### 6.3 Divergences from the reference

| # | Reference | lauren-di | Why |
|---|-----------|-----------|-----|
| 1 | `value`/`existing` force `SINGLETON` (`E-304`) | inherit the target's scope | §1.2: a request double must not outlive the request |
| 2 | An override can mask `DIScopeViolationError` (`E-303b`) | validation runs against the effective scope, so the violation survives | §1.3: green tests must not hide red startups |
| 3 | `values` flips a row to `multi` (`E-305`) | `OverrideError(non_multi_values)` | §1.4: never change a token's resolution type silently |
| 4 | Payload validated during `register` (`E-316`) | validated at plan construction; target existence at `compile()` | §1.5: order-independent, and no half-registered state |
| 5 | `detail["reason"] == "override_target_missing"` | `"target_missing"` | PRD 05's reason vocabulary is a closed set; shorter key |
| 6 | `OverridePlan` mutable (`E-319` shows the read-once accident) | frozen mapping | OV-01 |
| 7 | Module substitution and HTTP roles | absent | non-goals §3 |

### 6.4 What an override never does

- It never mutates a user class or module — no monkeypatching, no attribute
  assignment (the reference states this for `_register_override`; lauren-di keeps
  it).
- It never changes another token's row. Overriding `Sender` leaves `Smtp`
  registered (`E-318c`), so a test can assert the real implementation is still
  reachable by its own token.
- It never widens visibility (OV-27), raises a scope (`OV-12`), or adds a
  `provides` binding the target did not have (OV-05).

### 6.5 Cost

| Operation | Cost | Bound |
|-----------|------|-------|
| Plan construction | validate each override's payload | `O(#overrides)` |
| Registration with a plan | one mapping lookup per bound token | `O(1)` per token |
| Registration without a plan | one `None` check | `O(1)` |
| `compile()` target check | set difference against applied tokens | `O(#overrides)` |
| Memory | one row per overridden token | `O(#overrides)` |

Production builds pass `overrides=None` and pay one identity check per
registration; resolution is untouched (PRD 06 §6.5's argument, applied again).

### 6.6 Evidence index

| Script | Ids | What they gate |
|--------|-----|----------------|
| `p1.py` | `E-301` … `E-306` | value rows, scope forcing, the masked scope violation, `values` flip, unmatched targets |
| `p2.py` | `E-307` … `E-314` | thread/task/nesting isolation, late registration, visibility, aliases, `multi`+`provides` preservation |
| `p3.py` | `E-315` … `E-321` | `use_class` DI + hooks, payload timing, factory injects, protocol tokens, plan mutation, shared plans, unhashable tokens |
| `p4.py` | `E-322` … `E-325` | which scope actually fires `@post_construct` |

---

## 7. Error Catalog Impact

One new leaf under `DIError`, and one amended row.

| # | Exception | `code` | Trigger | Raised by | `detail` | Message shape |
|---|-----------|--------|---------|-----------|----------|---------------|
| 33 | `OverrideError` | `override_error` | an override cannot be applied: unmatched target, bad payload, widening scope, duplicate token, or `values` on a single row | `ProviderOverride()`, `OverridePlan()`, `compile()` | `token`, `kind`, `reason` ∈ {`target_missing`, `bad_payload`, `scope_widen`, `duplicate`, `non_multi_values`}, `hint` | `cannot override {kind} {token}: {reason}` |

Amended rows (PRD 05 catalogue):

| Row | Change |
|-----|--------|
| 3 — `UnresolvableParameterError` | its trigger list loses one case: a bad `use_class` payload is now `OverrideError(bad_payload)` at plan-construction time, not this class at registration time (§1.5, D-45) |
| 12 — `MissingProviderError` | is deliberately *not* used for an unmatched override. The reference raises it with `reason="override_target_missing"` (`testing/_module.py:400–412`); lauren-di keeps one class per failure kind, so this stays `OverrideError` (OV-15) |
| 30/31 — visibility rows | unchanged; §5.8/OV-27 states that visibility still applies to override rows |

- **OV-33.** Every `OverrideError` names the token and the `kind`; a message
  without either is a bug in the raise site (T07-33; the family is
  T07-15…T07-19).
- **OV-34.** `OverrideError` is raised before any instance is constructed. No
  partially applied plan can be observed through the public API (T07-34, with
  T07-18 covering the half-registered case `E-316b` measures).

---

## 8. Phased Implementation Plan

| Phase | Ships in | Content | Exit criterion |
|-------|----------|---------|----------------|
| 1 — Data model | `0.1.0` | `ProviderOverride`, `OverridePlan`, `OVERRIDE_KINDS`, payload validation, freeze | T07-01…T07-04, T07-17 green |
| 2 — Application | `0.2.0` | `DIContainer(overrides=)`, row lowering for all five kinds, `applied_overrides()`, scope inheritance | T07-05…T07-14 green |
| 3 — Validation | `0.2.0` | `compile()` target check, `optional`, `non_multi_values`, `scope_widen` | T07-15…T07-19 green |
| 4 — Activation | `0.3.0` | `building_with`, `active_override_plan`, isolation tests | T07-20…T07-28 green |
| 5 — Interactions | `0.3.0` | visibility, hooks, teardown, `explain()` | T07-27…T07-30 green |
| 6 — Docs | `0.3.0` | testing guide, pytest example, isolation statement | docs build + `llms_check` green |

---

## 9. Documentation & Convention Updates

1. `docs/guides/testing.md` — the fixture pattern of §5.9, one example per kind,
   and the `session`-scope warning of OV-32.
2. `docs/reference/overrides.md` — `ProviderOverride` field table, the five kinds,
   §5.3's scope table, and the four CG statements verbatim.
3. `docs/guides/migration.md` — the mapping from `lauren.testing`:
   `override_provider(T).use_value(v)` becomes
   `OverridePlan({T: ProviderOverride(token=T, kind="value", value=v)})`;
   `override_module(...)` is listed as not supported, with D-37 as the reason.
4. `llms.txt` / `llms-full.txt` — five new public symbols
   (`OverridePlan`, `ProviderOverride`, `OVERRIDE_KINDS`, `building_with`,
   `active_override_plan`) plus the `overrides=` keyword and
   `applied_overrides()`.
5. `CLAUDE.md` / `AGENTS.md` — the isolation guarantee as a rule for generated
   tests: always a fresh container, never a shared one.
6. `CHANGELOG.md` — one "Added" entry per phase in §8.

---

## 10. Test Plan

### 10.1 Enumerated test cases

| Test | Rule | Asserts |
|------|------|---------|
| T07-01 | OV-01 | assigning `plan.providers[T]` raises; `ProviderOverride` is frozen |
| T07-02 | OV-02 | two containers from one plan resolve independently |
| T07-03 | OV-03 | `ProviderOverride` has no `role` attribute |
| T07-04 | OV-04 | `OverridePlan` has no `modules` attribute |
| T07-05 | OV-05 | `multi` and `provides` identical to the target's |
| T07-06 | OV-06 | `values` on a single row raises `non_multi_values` |
| T07-07 | OV-07 | `use_class` fake receives injected deps; its hooks fire per scope |
| T07-08 | OV-08 | `injects` positional order; `OptionalDep` → `None` |
| T07-09 | OV-09 | alias resolves in the alias's domain |
| T07-10 | OV-10 | protocol token overridden; concrete token untouched |
| T07-11 | OV-11 | `REQUEST` target stays `REQUEST` for `value` and `existing` |
| T07-12 | OV-12 | widening `scope=` raises `scope_widen` |
| T07-13 | OV-13 | `get_provider().scope` is the effective scope for all five kinds |
| T07-14 | OV-14 | `TRANSIENT` value stable, `get_provider().scope == TRANSIENT` |
| T07-15 | OV-15 | unmatched non-optional fails `compile()` with `target_missing` |
| T07-16 | OV-16 | unmatched `optional` is silent and absent from `applied_overrides()` |
| T07-17 | OV-17 | each malformed payload raises `bad_payload` at construction |
| T07-18 | OV-18 | the same failure raises the same class regardless of registration order |
| T07-19 | OV-19 | duplicate token named in the message |
| T07-20 | OV-20 | plan-free context, `overrides=` alone suffices |
| T07-21 | OV-21 | `active_override_plan()` inside the block |
| T07-22 | OV-22 | `None` after an exception |
| T07-23 | OV-23 | outer/inner/outer |
| T07-24 | OV-24 | activating a plan after building does not change the container |
| T07-25 | OV-25 | exact `frozenset`, and it is not live |
| T07-26 | OV-26 | late registration is overridden |
| T07-27 | OV-27 | override row invisible outside its module |
| T07-28 | OV-28 | hook timing per scope for `class` overrides |
| T07-29 | OV-29 | built row torn down in reverse order; value never torn down |
| T07-30 | OV-30 | `explain()` contains `override=` |
| T07-31 | OV-31 | the overridden test builds its own container, the fixture's stays clean |
| T07-32 | OV-32 | the docs' `session`-scope warning is present (docs-contract test) |
| T07-33 | OV-33 | every raise site carries `token` and `kind` |
| T07-34 | OV-34 | no instance is constructed before the raise (constructor counter) |

### 10.2 Concurrency tests (the CG statements)

| Test | Statement | Method |
|------|-----------|--------|
| T07-CG1 | CG-1 | one plan, two containers, same token, different resolutions |
| T07-CG2 | CG-2 | `threading.Barrier` + two threads, each `building_with` a different plan; assert each container's value and that the main thread's plan is `None` |
| T07-CG3 | CG-3 | `asyncio.gather` of two builders that `await` between setting the plan and constructing; assert both results |
| T07-CG4 | CG-4 | exception inside the block; nested blocks; assert the variable after each |

These four run under the `integration` marker (nox session `tests_integration`)
because they use real threads and a real event loop.

### 10.3 Acceptance traceability for this PRD

| Requirement | Where |
|-------------|-------|
| `OverridePlan` / `ProviderOverride` semantics | §5.1, OV-01…OV-04 |
| Five override kinds | §5.2 (one worked example per kind), OV-05…OV-10 |
| Optional overrides that never match | §5.4, OV-15/OV-16, T07-15/T07-16 |
| Scope inheritance | §5.3, OV-11…OV-14, T07-11…T07-14 |
| ContextVar-scoped activation per build | §5.5, OV-20…OV-24, T07-20…T07-24 |
| Thread/task isolation | §5.6, CG-2/CG-3, T07-CG2/T07-CG3 |
| Pytest fixture pattern | §5.9, OV-31/OV-32 |
| Concurrency guarantee statement | §5.6 (CG-1…CG-4) |
| Test plan proving no leakage between builds | §10.2 |
