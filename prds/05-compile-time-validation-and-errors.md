# PRD 05 — Compile-Time Validation & Errors

| Field | Value |
|-------|-------|
| Status | Draft |
| Owner PRD | 05 |
| Depends on | 01 (`DIError` root, exported leaves), 02 (registration, `Provider`, signature introspection), 03 (`DIScopeViolationError`, `LifecycleViolationError`, `DestructError`), 04 (`Token`, aliases, `multi`, ambiguity shapes) |
| Depended on by | 06 (visibility errors), 07 (override validation), 08 (migration mapping) |
| Reference implementation | `../lauren-framework/lauren/_di/__init__.py` (`compile` 978, `resolve_one` 1015, `_check_scope` 986, `_get_injectable_meta` 1585, `_inspect_callable_deps` 1965), `lauren/exceptions.py` (all leaves), `lauren/_di/custom.py` (`_validate_token` 270) |
| Resolves | D-31 … D-36 |

This PRD owns the moment a graph stops being a pile of registrations and becomes checkable: what
`compile()` does, in what order, what it refuses to do, and every exception the library can raise with
its code, message shape, `detail` keys and remediation.

Everything here is derived from reading the reference implementation and running it. Probe scripts live
in `/tmp/prd05/` and are cited as `probe E-nn`; the `E-` series continues PRD 04's numbering, so no probe
ID is reused across PRDs.

## 1. Problem Statement

### 1.1 In the reference, validation is something the embedder must remember

`DIContainer.compile()` exists and does real work, but nothing obliges anyone to call it. `resolve()`
does not compile first, so a container that is never compiled accepts a cyclic graph and reports it as
`RecursionError` deep inside the resolver, and accepts a `SINGLETON → REQUEST` edge silently — the rule
PRD 03 exists to enforce simply never runs. A library cannot inherit that contract: the embedder is a
user of `lauren-di`, not a framework that owns a startup phase.

The measured consequences (probe E-110, E-116):

| Without an explicit `compile()` | Result |
|--------------------------------|--------|
| Cyclic graph resolved | `RecursionError` from the resolver, no cycle path |
| `SINGLETON` holding a `REQUEST` dependency | resolves; the singleton keeps a request instance forever |
| `list[T]` with a non-`multi` member | resolves or raises depending on which path is taken |
| Missing dependency behind a default | silent, which is correct — but indistinguishable from the unchecked case |

### 1.2 Which error surfaces depends on registration order

The reference interleaves graph traversal with validation: `compile()` runs one depth-first walk that
raises cycles, missing providers, scope violations and one flavour of ambiguity as it goes, then sweeps
`_token_bindings` afterwards for a second flavour of ambiguity. So the error a user sees is a function
of dict insertion order, and the same graph can report two different problems depending on which
provider was registered first.

Two measurements make this concrete:

| Graph | Reference result | Probe |
|-------|------------------|-------|
| Cycle between `C1`/`C2` **and** two providers bound to `TOK` | `CircularDependencyError` — whichever was registered first | E-108 |
| One consumer depending on the ambiguous `TOK`, no cycle | `ProtocolAmbiguityError` with `detail={"count": 2}` — no token named | E-117 |
| Same ambiguity, nobody depending on `TOK` | `ProtocolAmbiguityError` with `detail={"token": "Token(PA)", "count": 2}` | E-107 |

Three shapes for two situations, and the richer shape is the one a user is *less* likely to hit. A
catalogue cannot be written against behaviour that changes with insertion order, so this PRD fixes an
order and then documents it.

### 1.3 `detail` is not a contract in the reference

The same leaf carries different keys at different raise sites. `MissingProviderError` has four shapes
in the reference (`{"token"}`, `{"alias","target"}`, `{"token","module"}`, and `{}` for a bare runtime
lookup — probes E-104, E-114, E-115, E-119). `ProtocolAmbiguityError` has five. `MetadataInheritanceError`
and `DuplicateBindingError` carry nothing at all (probes E-111, E-128). A caller that wants to render
its own diagnostic has to parse the message, and the message for the same class is worded differently
at each site.

### 1.4 The carried set and the dropped set are both undocumented

`lauren/exceptions.py` defines 46 classes. `lauren-di` needs 13 of them plus a root. Deciding *which*
13 is the whole point of PRD 01 §7's carried / not-carried split, but that split was written before PRDs
02–04 fixed their own triggers, and two rows have since drifted:

- `LifecycleViolationError` was listed as *not carried*, yet D-23 makes it the guard for a single-use
  `ResolutionScope` (PRD 03 §5.6); PRD 01 §7 now carries it.
- PRD 01 §7 originally required that no leaf named `…Error` be "returned", while D-20 makes
  `DestructError` a returned value; PRD 01 §7 now states that exception.

Both are reconciled here (§7), because the catalogue is the document that has to be internally
consistent.

## 2. Goals

| ID | Goal | Measured by |
|----|------|-------------|
| G1 | Validation runs without being asked | first `resolve()` compiles (D-14); `compile()` stays public and idempotent |
| G2 | The same graph reports the same error, every time | fixed phase order; order-independence tested by registering the same graph in four permutations (T05-08 … T05-11) |
| G3 | Every carried leaf has one documented code, one message shape and one `detail` shape per trigger | the catalogue in §5.6; T05-46 |
| G4 | `detail` is machine-readable | JSON round-trip assertion on every raised error; T05-47 |
| G5 | The graph is never executed to validate it | zero constructions, zero awaits during `compile()`; T05-06 |
| G6 | Declaration errors are not postponed to validation | D-36; T05-49 |
| G7 | The dropped exceptions are named, with the reason and the replacement | §5.8 table; T05-50 |
| G8 | Every rule is testable through public names only | §10; assertions use `pytest.raises`, `.code`, `.detail`, `.message` |
| G9 | Validation cost is linear in providers plus edges | one pass per phase; §6.5 |

## 3. Non-Goals

| Not in this PRD | Where it lives |
|-----------------|----------------|
| Which registrations are legal, and what `Provider` contains | PRD 02 |
| Scope *semantics* (caching, teardown, hooks) and the six-cell narrowing table | PRD 03 |
| Token equality, alias resolution rules, `multi` / `list[T]` collection semantics | PRD 04 |
| Visibility filtering and `owning_module` semantics | PRD 06 — this PRD only consumes `visible_bindings()` as a filter |
| Override validation (`OverridePlan` may not match anything) | PRD 07 |
| The `lauren` → `lauren_di` symbol map and parity harness | PRD 08 |
| A diagnostic *renderer* (pretty-printed graph, `--explain` output) | out of scope for v1; `detail` is the data a renderer would need |
| Multi-error aggregation ("report every problem at once") | explicitly rejected, D-31 |
| Any HTTP, extractor, module-graph or scheduling error | framework-only, §5.8 |

Validation is deliberately **not** a linter. It answers one question — "can every registered provider
be constructed from what is registered, in a lifetime that is legal?" — and stops there.

## 4. Prior Art

| System | What it does | What `lauren-di` takes |
|--------|--------------|------------------------|
| NestJS | No validation at all; a missing provider fails at request time with "Nest can't resolve dependencies of the X" | The message *shape* — naming the consumer and the parameter — is worth keeping. The timing is not (G1) |
| .NET `Microsoft.Extensions.DependencyInjection` | `ValidateOnBuild` / `ValidateScopes` are opt-in flags on the host builder; without `ValidateScopes`, a singleton capturing a scoped service is silently promoted to a singleton | The proof that scope capture must be a *validation* error (PRD 03) and that opt-in validation is the wrong default for a library |
| Spring | No build-time check; `@Autowired` failures surface at context refresh, sometimes at first use; cycles are allowed by default and only rejected under `spring.main.allow-circular-references=false` | The negative lesson: a configurable "validate?" switch is a coin flip every user loses once. Cycles are always an error here |
| Guice | Detects cycles and missing bindings at injector creation (which is a build step) and reports the full dependency *path* to the failing node | The path, not just the endpoint: `detail["cycle"]` is an ordered list, and every missing-provider message names the consumer |
| Dagger / Wire | Validation is a compile-time codegen step; impossible graphs do not compile | The correct instinct — but it needs codegen. `lauren-di` gets the same guarantee from one linear pass at first resolve (G9) |
| FastAPI | Validates the dependency graph per route at import time and raises `FastApiError` naming the parameter | The parameter-level naming (`detail["param"]`); everything HTTP-bound is out of scope |
| `typing.get_type_hints` / `inspect.signature` | Report unresolvable forward references late and vaguely | PRD 02 resolves annotations at registration; this PRD treats a leftover string as a real token (V-33) and never re-resolves it |

## 5. Proposed API

### 5.1 `compile()` — the contract

```python fragment
from lauren_di import DIContainer

container = DIContainer()
container.register(Config)
container.register(Repo)

container.compile()                     # validates; returns None
container.compile()                     # no-op: nothing changed
container.register(Cache)               # invalidates (D-15)
container.compile()                     # re-validates the whole graph
```

Rules:

1. **Synchronous and total.** `compile() -> None`. Validation is pure inspection, so there is no
   `compile_async()` and no await anywhere in the pipeline (D-32). Because `resolve()` is async, the
   lazy path in rule 5 runs the same synchronous pipeline before its first `await`.
2. **Idempotent, not incremental.** A second call with no registration in between returns immediately.
   There is no partial revalidation: the unit of validation is the whole container.
3. **Invalidated by registration.** Every `register`, `register_value`, `register_class`,
   `register_factory`, `register_alias` and `register_custom` clears the compiled flag (D-15), so the
   next `compile()`/`resolve()` re-runs all six phases. The reference keeps `_compiled=True` and never
   revalidates (probe E-110).
4. **A failed compile leaves the container uncompiled.** The flag is set only on success, so the same
   graph can be fixed and retried, and a broken container never pretends to be validated
   (probe E-120).
5. **`resolve()` compiles first.** The first `resolve()` (or `resolve(list[T])`) validates under D-14;
   `compile()` is the explicit door to the same pipeline.
6. **Lookup methods never compile.** `get_provider`, `has_provider`, `registered_providers`,
   `all_providers` and `instance_index` are registry reads. A caller that wants a
   guarantee calls `compile()`; a caller that wants one provider does not pay for the graph
   (probe E-116).
7. **Fail-fast, single error.** The first problem found is raised; nothing is collected and no
   aggregate exception type exists (D-31). Fixing a dependency graph is a loop — fix, rerun, fix — and
   a partial failure set invites users to fix the last item only.

### 5.2 The six phases

Validation is an ordered pipeline. Each phase reads one view of the registry and raises one class.
The order is the contract (D-31): declaration problems are reported before linkage problems, linkage
before topology, topology before completeness, and lifetime last.

| # | Phase | Reads | Raises | `detail` | Why here |
|---|-------|-------|--------|----------|----------|
| 1 | Token index | `registered_providers()`, `provides` | `ProtocolAmbiguityError` | `token`, `count`, `hint` | Two providers claiming one token is a *declaration* mistake; it is decidable without touching an edge |
| 2 | Alias targets | every `provider_kind == "existing"` row | `MissingProviderError`, `CircularDependencyError` | `alias`, `target`, `hint` / `cycle` | An alias is a pointer; a dangling pointer is cheaper to report than a walk that dies on it |
| 3 | Cycles | the edge set from phase 4's walk | `CircularDependencyError` | `cycle` | A cycle makes every downstream message unreadable; report it before the damage |
| 4 | Missing providers | same walk, edges only | `MissingProviderError` | `token`, `dependent`, `param`, `hint` | Completeness assumes the graph is acyclic, so it runs after phase 3 |
| 5 | Scope narrowing | every edge plus both scopes | `DIScopeViolationError` | `dependent`, `dependency`, `dependent_scope`, `dependency_scope` | Lifetime is the weakest signal: it is only meaningful once the graph is complete |
| 6 | Finalise | the registry | — | — | Precompute the instance index once; mark compiled |

Two things are deliberately *not* phases, because they cannot survive registration (D-36):

- **Inherited metadata rejection** (`MetadataInheritanceError`) is raised by `injectable_meta()` and
  `register()`, before a provider exists (probe E-111).
- **Unresolved parameters** (`UnresolvableParameterError`) are raised by `register()` while the
  signature is being inspected (probe E-112), for all three triggers — no annotation, positional-only
  (D-19), bare `Token` class-body field (D-30).

A container that reaches phase 1 therefore has only *well-formed* providers in it, which is why the
pipeline needs no metadata phase. §5.4 states exactly what the walk reads.

The edge set for phases 3–5 is:

```
edges(provider) = { (param, token) for param, token in provider.deps }
                ∪ { (field, token) for field, token in provider.field_deps }
                ∪ { alias edge → alias_target }                 if provider_kind == "existing"
                ∪ { one edge per member }                        if token is list[T]
```

An edge that no provider satisfies is skipped when the parameter has a default or is wrapped in
`OptionalDep`; when the provider *does* exist, the edge participates in phases 3–5 like any other
(V-27, V-30, V-31).

### 5.3 When validation runs

```
import            decorate            register*                 compile()          resolve()
  │                  │                   │                        │                  │
  ├─ @injectable() ──┤                   │                        │                  │
  │   DecoratorUsageError               │                        │                  │
  │   LifecycleConfigError              │                        │                  │
  │                                     ├─ DuplicateBindingError │                  │
  │                                     ├─ MetadataInheritance   │                  │
  │                                     ├─ UnresolvableParameter │                  │
  │                                     ├─ InvalidProviderError  │                  │
  │                                     └─ clears compiled flag  │                  │
  │                                                              ├─ phase 1 … 6 ────┤
  │                                                              └─ first resolve ───┘
```

| Moment | What can fail | Owning PRD |
|--------|---------------|------------|
| Decoration | `DecoratorUsageError`, `LifecycleConfigError` | 02, 03 |
| Registration | `DuplicateBindingError`, `MetadataInheritanceError`, `UnresolvableParameterError`, `InvalidProviderError`, `LifecycleConfigError`, `DecoratorUsageError` (custom providers) | 02, 04 |
| `compile()` / first `resolve()` | `ProtocolAmbiguityError`, `MissingProviderError`, `CircularDependencyError`, `DIScopeViolationError` | 05 |
| Later `resolve()` | `MissingProviderError` (uncompiled container), `DIScopeViolationError` (strict request), `LifecycleViolationError` (closed scope) | 02, 03, 05 |
| Teardown | `DestructError` — returned, not raised | 03 |

The pipeline is reachable exactly twice: explicitly through `compile()`, and implicitly through the
first `resolve()`. Nothing else in the public API triggers it — in particular a `DIContainer` built
entirely from `register_value` calls never runs phases 2–5 at all, because it has no edges.

### 5.4 What validation reads

Validation reads four things and nothing else:

| Input | Field | Used by |
|-------|-------|---------|
| The token index | `registered_providers()`, `provides` | phase 1 |
| The alias graph | `provider_kind`, `alias_target` | phases 2, 3 |
| The edge set | `deps`, `field_deps`, `optional_dep_names`, `positional_arg_names` | phases 3, 4, 5 |
| The lifetimes | `provider.scope` for both ends of every edge | phase 5 |

It does **not** read: `factory`, `post_construct`, `pre_destruct`, the hook tuples, `owning_module`
(PRD 06 owns the filter), `value` payloads, or anything a provider might construct. `factory` is
inspected during registration (PRD 02) and never during validation — that is what makes G5 true.

> The reference skips framework-provided tokens (`Request` and `framework_value_types()`) during the
> walk. `lauren-di` has no such tokens: an unregistered annotation is a missing provider, full stop.
> PRD 08 maps the seam for migrations.

Two edge cases worth stating because they are easy to get wrong:

- **`positional_arg_names`** exists so phase 4 can tell a factory's synthetic parameter names
  (`__factory_arg_0`) from real ones. It is read only when building a message.
- **A `list[T]` dependency** expands to one edge per visible member, so a cycle through any single
  member is caught, and a scope violation on any member is caught, independently (V-26, V-37).

### 5.5 `DIError` — the root

```python fragment
from lauren_di import DIError

class DIError(Exception):
    code: str = "lauren_di_error"      # stable, snake_case, one per leaf
    def __init__(self, message: str = "", *, detail: dict[str, Any] | None = None) -> None: ...
    message: str                        # the human sentence
    detail: dict[str, Any]              # the machine-readable facts, never {}
    def __str__(self) -> str: ...       # returns self.message
```

Contract:

1. **One root.** Every leaf in §5.6 subclasses `DIError` directly or indirectly, so `except DIError`
   is a complete catch and `except MissingProviderError` still works (D-18).
2. **`message` is a sentence, not a report.** `str(exc) == exc.message`, no traceback formatting, no
   newline, no trailing period. Multi-line diagnostics belong to a renderer (§3).
3. **`detail` defaults to `{}` and is always a real dict.** It is never `None`, never shared between
   instances, and never contains an exception object or a class — see rule 4 of §5.7.
4. **`code` is public API.** It is a class attribute, stable across versions, and equal on every
   instance of a leaf. Two leaves never share a code.
5. **Constructible by hand.** `raise DIError("x", detail={"k": 1})` is legal; the library does it for
   no leaf, but a test double or a wrapper may.

### 5.6 The catalogue

Thirteen leaves plus the root. Rows are per *trigger*, so a leaf that can be raised from two very
different situations (ambiguity at validation vs at resolve) appears twice; the code, class and
hierarchy are shared.

#### 5.6.1 Hierarchy

```
DIError
├── InvalidProviderError
├── DecoratorUsageError
├── MissingProviderError
├── DuplicateBindingError
├── ProtocolAmbiguityError
├── CircularDependencyError
├── DIScopeViolationError
├── UnresolvableParameterError
├── MetadataInheritanceError
├── LifecycleConfigError
├── LifecycleViolationError
└── DestructError
```

Every leaf is a direct child of `DIError`. The reference's `StartupError` / `LifecycleError` mid-layers
exist to mark "not HTTP-mapped"; with the HTTP family gone (PRD 01 §7) they carry no information, so
they are not carried. A flat hierarchy also means `except DIError` and `except SomeLeaf` are the only
two useful statements, which is the whole point of D-18.

#### 5.6.2 Carried leaves

| # | Leaf | `code` | Trigger | Raised by | `detail` | Message shape | Remediation | Test |
|---|------|--------|---------|-----------|----------|---------------|-------------|------|
| 1 | `DIError` | `lauren_di_error` | raised directly by user or wrapper code | anywhere | caller's | caller's | raise a leaf instead; `DIError` is the catch-all, not a thing to construct | T05-46 |
| 2 | `InvalidProviderError` | `invalid_provider` | target is neither a class nor a callable | `register` | `target`, `kind` | `Cannot register {target}: expected a class or a callable` | pass the class or the function object, not an instance of it | T05-41 |
| 3 | `DecoratorUsageError` | `decorator_usage` | bare `@injectable` (no parentheses) | `injectable` | `decorator`, `target` | `@injectable must be called: write @injectable()` | add the parentheses: `@injectable()` | T05-43 |
| 4 | `DecoratorUsageError` | `decorator_usage` | `None` or unhashable token / target | `use_value`, `use_class`, `use_factory`, `use_existing`, `register_alias` | `token` | `{helper}: provider token {token} is not hashable` | use a class, a `str` or a `Token`; `None` is never a token | T05-43 |
| 5 | `DecoratorUsageError` | `decorator_usage` | `use` is not a class, `factory` is not callable | `use_class`, `use_factory` | `use` / `factory` | `useClass: use must be a class, got {use}` | pass a class to `use=`, a callable to `factory=` | T05-43 |
| 6 | `DecoratorUsageError` | `decorator_usage` | alias token equals its target | `use_existing`, `register_alias` | `token`, `reason="self_alias"` | `{token} cannot alias itself` | alias a different token, or drop the alias registration | T05-43 |
| 7 | `MissingProviderError` | `missing_provider` | target is not marked `@injectable` | `register` | `token` | `{token} is not marked @injectable` | decorate with `@injectable()`, or bind it explicitly with `register_class` / `use_class` | T05-29 |
| 8 | `MissingProviderError` | `missing_provider` | an edge has no provider | `compile`, first `resolve` | `token`, `dependent`, `param`, `hint`; `module` under encapsulation (PRD 06) | `No provider for {token} (required by {dependent}.{param})` | register the token, give the parameter a default, or wrap it in `OptionalDep` | T05-29 |
| 9 | `MissingProviderError` | `missing_provider` | `list[T]` with no provider for `T` | `compile`, `resolve` | `token="list[T]"`, `element`, `dependent`, `param` | `No provider for {element} (required as {token} by {dependent}.{param})` | register at least one provider for the element with `multi=True` | T05-34 |
| 10 | `MissingProviderError` | `missing_provider` | alias target unknown | `compile` | `alias`, `target`, `hint` | `Alias {alias} -> {target} points at an unknown token` | register the target before the alias, or drop the alias | T05-19 |
| 11 | `MissingProviderError` | `missing_provider` | lookup of an unregistered token | `resolve`, `get_provider` | `token` | `No provider for {token}` | check `has_provider()` first, or register the token | T05-07 |
| 12 | `DuplicateBindingError` | `duplicate_binding` | second **non-`multi`** binding for one token | `register`, `register_*`, `use_*` | `token`, `existing`, `new` | `{token} is already registered` | remove one binding, or mark both `multi=True` and depend on `list[T]` | T05-42 |
| 13 | `ProtocolAmbiguityError` | `protocol_ambiguity` | >1 non-`multi` binding **in one visibility domain** | `compile` phase 1 | `token`, `count`, `hint`; `modules` under encapsulation (PRD 06) | `Multiple bindings for {token} ({count})` | keep one binding, or mark them `multi=True` and depend on `list[T]` | T05-14 |
| 14 | `ProtocolAmbiguityError` | `protocol_ambiguity` | >1 non-`multi` binding reached through an edge | `compile` phase 4 | `token`, `count`, `dependent`, `param`, `hint` | `Multiple bindings for {token} ({count}), required by {dependent}.{param}` | as row 13; `dependent` + `param` name the site that asked for the token | T05-15 |
| 15 | `ProtocolAmbiguityError` | `protocol_ambiguity` | scalar resolve of a token with >1 visible binding | `resolve` | `token`, `count`, `hint` | `Multiple bindings for {token} ({count}); resolve(list[{token}])` | depend on `list[T]` when more than one binding is intended | T05-16 |
| 16 | `ProtocolAmbiguityError` | `protocol_ambiguity` | `list[T]` where a member is not `multi` | `compile`, `resolve` | `token="list[T]"`, `element`, `count`, `hint` | `{token} requires every provider for {element} to be registered with multi=True` | add `multi=True` to every provider for the element, or drop the collection request | T05-17 |
| 17 | `CircularDependencyError` | `circular_dependency` | provider cycle | `compile` phase 3 | `cycle` | `Circular dependency: {a} -> {b} -> {a}` | break the cycle with a factory, a `use_value` row, or an injected token | T05-23 |
| 18 | `CircularDependencyError` | `circular_dependency` | self-cycle | `compile` phase 3 | `cycle` | `Circular dependency: {a} -> {a}` | a provider cannot depend on its own token; extract the shared part | T05-24 |
| 19 | `CircularDependencyError` | `circular_dependency` | alias cycle or alias into a provider cycle | `compile` phases 2, 3 | `cycle` | as above | point the alias at a provider, not at another alias | T05-21 |
| 20 | `DIScopeViolationError` | `di_scope_violation` | consumer outlives its dependency | `compile` phase 5, first `resolve` | `dependent`, `dependency`, `dependent_scope`, `dependency_scope` | `{DependentScope} {dependent} depends on {dependency_scope}-scoped {dependency}` | narrow the consumer's scope, widen the dependency's, or inject a factory instead of an instance | T05-35 |
| 21 | `DIScopeViolationError` | `di_scope_violation` | `REQUEST` resolved with no active scope, strict mode | `resolve` | `token`, `reason="no_active_scope"` | `{token} is request-scoped but no scope is active` | open a `ResolutionScope` before resolving, or use lax mode (PRD 03) | T05-38 |
| 22 | `UnresolvableParameterError` | `unresolvable_parameter` | parameter has neither annotation nor default | `register` | `target`, `param`, `hint` | `{target} parameter {param} is missing an annotation` | annotate the parameter, or give it a default | T05-40 |
| 23 | `UnresolvableParameterError` | `unresolvable_parameter` | positional-only parameter that would be collected (D-19) | `register` | `target`, `param`, `hint` | `{target} parameter {param} is positional-only` | make it keyword-or-positional, or give it a default | T05-40 |
| 24 | `UnresolvableParameterError` | `unresolvable_parameter` | bare `Token` class-body field (D-30) | `register` | `target`, `param`, `hint` | `{target} field {param} is a bare Token; annotate it as Annotated[T, Inject(token)]` | annotate as `Annotated[T, Inject(token)]` | T05-40 |
| 25 | `MetadataInheritanceError` | `metadata_inheritance` | undecorated subclass reads an inherited marker | `register`, `injectable_meta` | `target`, `base` | `{target} inherited @injectable metadata from {base}; re-decorate explicitly` | re-decorate the subclass with `@injectable()` | T05-39 |
| 26 | `LifecycleConfigError` | `lifecycle_config` | generator provider with `Scope.TRANSIENT` (D-17) | `register` | `provider`, `hint` | `Generator provider {provider} cannot be transient: nothing would close it` | use `Scope.SINGLETON` or `Scope.REQUEST` for a generator provider | T05-44 |
| 27 | `LifecycleConfigError` | `lifecycle_config` | `@post_construct` / `@pre_destruct` on a non-callable (PRD 03 §6.3) | the decorator | `target` | `{target} is not a method` | decorate the method definition, not the call | T05-44 |
| 28 | `LifecycleViolationError` | `lifecycle_violation` | scope used before `async with` or after `close()` | `ResolutionScope.resolve`, `__aenter__` | `state` | `scope is {state}; use it inside async with` | use the scope inside `async with`; a closed scope has no cache | T05-48 |
| 29 | `DestructError` | `destruct_error` | a hook or `aclose()` failed during teardown — **returned**, never raised | `close()`, `shutdown()` | `class`, `stage`, `origin`, `cause` | `{stage} hook on {class} failed: {cause}` | nothing at runtime: log the value and fix the hook | T05-48 |
| 30 | `MissingProviderError` | `missing_provider` | the token exists but is not visible from the consumer's module domain | `compile`, `resolve`, `get_provider` | `token`, `module`, `reason="not_visible"`, `hint`; `dependent` + `param` on an edge | `No provider for {token} visible from module {module}` | grant the token to that module, or resolve through the module that declares it | T06-16 |
| 31 | `MissingProviderError` | `missing_provider` | the consumer's module has no visibility declaration while encapsulation is on | `compile`, `resolve` | `token`, `module`, `reason="undeclared_module"`, `hint` | `module {module} has no declared visibility` | call `set_visible({module}, ...)` before resolving | T06-17 |
| 32 | `DecoratorUsageError` | `decorator_usage` | `owning_module` is not a class | every `register*`, `use_*`, `resolve` | `token`, `owning_module` | `owning_module must be a class, got {owning_module}` | pass the module class itself, or omit the keyword | T06-03 |
| 33 | `OverrideError` | `override_error` | an override cannot be applied: unmatched target, malformed payload, widening scope, duplicate token, or `values` on a single row | `ProviderOverride()`, `OverridePlan()`, `compile()` | `token`, `kind`, `reason` ∈ {`target_missing`, `bad_payload`, `scope_widen`, `duplicate`, `non_multi_values`}, `hint` | `cannot override {kind} {token}: {reason}` | fix the plan: register the token, correct the payload, drop `scope=`, or use `use_value` for a single row | T07-15 … T07-19 |

Rows 30–32 are added by PRD 06 (module visibility, D-37 … D-43). Their tests are enumerated in
PRD 06 §10.1, so this catalogue's rule "every row names the test that asserts it" holds across
documents: rows 1–29 cite `T05-nn`, rows 30–32 cite `T06-nn`. Rows 8, 9, 13, 14 and 16 above gained
the `module` / `modules` keys for the same reason — under encapsulation every "missing" and every
"ambiguous" verdict is relative to a domain.

Row 33 is added by PRD 07 (testing overrides, D-44 … D-49), and cites `T07-15` … `T07-19`. It is the
one leaf that is raised *before* the container exists: payload errors come from `ProviderOverride()` /
`OverridePlan()`, and only `target_missing` needs `compile()`. Two consequences are normative here
rather than in PRD 07: row 3 (`UnresolvableParameterError`) no longer covers a bad `use_class`
payload, and row 12 (`MissingProviderError`) is deliberately *not* used for an unmatched override,
even though the reference raises it for that case with `reason="override_target_missing"`.

Rows 7, 11, 21 and 28 are the four "runtime, not validation" shapes; they are in the catalogue
because a caller handling `MissingProviderError` must be able to tell them apart, and the `detail` keys
are how. Rows 30 and 31 are the two *validation* shapes of the same leaf that a caller must also tell
apart, and they use `reason` for exactly that.

#### 5.6.3 Per-leaf notes that the table cannot carry

**`MissingProviderError` is the only leaf with seven triggers.** The four validation/registration
shapes all carry `token`; the alias shape carries `alias` + `target` instead, because there is no
single token to name — the user needs both ends. `dependent` and `param` are added by D-34 whenever an
edge discovered the token, so a caller can highlight the consumer's source line. `module` is added by
PRD 06 whenever the verdict is relative to a domain, and `reason` distinguishes "not visible"
(`"not_visible"`) from "this module declared nothing" (`"undeclared_module"`). A bare lookup
(`resolve`, `get_provider`) carries `token` only: there is no edge to name.

**`ProtocolAmbiguityError` never carries the bare word `"list"`.** When the request was a collection,
`token` is the rendered `list[T]` and `element` carries `T` (D-29, PRD 04 §5.8). The validation form
adds `hint="register with multi=True, or depend on T"`; the resolve form adds
`hint="resolve(list[{token}])"`.

**`CircularDependencyError.cycle`** is the ordered token path, rendered, closed on the token that
repeats: `["Repo", "Db", "Repo"]`. It starts at the *first repeated* token, not at the provider the
walk entered from, so `Entry -> L1 -> L2 -> L1` reports `["L1", "L2", "L1"]` (probe E-118). Phase 2
reuses the same class and shape for alias cycles (probe: `["A", "B", "A"]`).

**`DIScopeViolationError` is one class with two very different shapes** (rows 20, 21). Validation
answers "who outlives whom" and needs all four scope fields; the runtime strict-request case answers
"you asked for a request instance and there is no request" and needs only the token. Both are the same
mistake class — using a lifetime that is not available — so they share a leaf, and the presence of
`reason` distinguishes them.

**`UnresolvableParameterError` is always a registration error** (D-36). All three triggers are decided
while inspecting the signature, before a `Provider` exists, so `compile()` can never raise it. `param`
is the parameter or field name; `hint` is the fix, and it is the only place the message differs between
triggers.

**`MetadataInheritanceError`** names both classes in the message and in `detail`; `hint` says
"re-decorate the subclass". It is raised by `injectable_meta(sub)` as well as by `register(sub)`
(probe E-111), because reading the marker is the actual failure.

**`DestructError` is a value** (D-35). It is an exception subclass so `except DIError` covers it, and
so a returned error can be logged like one, but teardown never raises it: `close()` / `shutdown()`
return `tuple[DestructError, ...]` (PRD 03 §5.8). The four `detail` keys are fixed by PRD 03.

### 5.7 Message and detail conventions

1. **One renderer.** Tokens are rendered by one private function used everywhere:
   `Plugin`, `"SETTINGS"`, `Token('DB', unique=False)`, `list[Plugin]`, `<lambda>`. The reference's
   renderer drops generic parameters and quotes (`list`, `SETTINGS`, `Token(DB)` — probe E-101); that
   makes two different tokens print identically, so D-33 fixes the rendering per PRD 04 §5.8 rule 3.
2. **`detail` is JSON-serialisable.** Strings, ints, booleans, `None`, and lists of those. No classes,
   no exception objects, no sets, no `Token` instances. `json.dumps(exc.detail)` must succeed for every
   error the library raises (T05-47).
3. **`dependent` is the consumer's token**, rendered; `param` is its parameter or field name. Together
   they are the edge. For a `list[T]` edge, `dependent`/`param` point at the consumer, and `element`
   names the member type.
4. **`hint` is advisory prose, and the machine-readable twin of the Remediation column.** It is a
   short imperative sentence, never parsed, and absent where there is nothing to say. It is the only
   `detail` value that is allowed to change wording between minor versions.
5. **Messages never end with a period and never contain a newline.** A remediation clause, when the
   message has one, is a second sentence introduced by `; ` or a parenthesis — `hint` carries the same
   advice structurally.
6. **No error wraps another.** When a hook raises, PRD 03 either propagates it untouched or records it
   in a `DestructError.cause`; the DI leaves never chain exceptions. A `MissingProviderError` raised
   while inspecting a signature is not re-wrapped as `UnresolvableParameterError`.

### 5.8 Framework-only exceptions

Every class in `lauren/exceptions.py` that `lauren-di` does **not** carry, with the reason and, where
one exists, the replacement. This table is the answer to "is `X` supported?" for migrations (PRD 08).

| Class | Family | Why not carried | Replacement |
|-------|--------|-----------------|-------------|
| `LaurenError` | root | replaced by `DIError`; its `status_code`, `headers` and `to_payload()` are HTTP response concerns | `DIError` |
| `StartupError` | root | a mid-layer that only marks "not HTTP-mapped"; with the HTTP family gone it distinguishes nothing | none — flat hierarchy, D-31 |
| `LifecycleError` | root | same; `DestructError` / `LifecycleViolationError` sit directly under `DIError` | none |
| `HTTPError` | HTTP | there is no response to map to | none |
| `ExtractorError` | HTTP | request parsing | none |
| `ExtractorFieldError` | HTTP | request parsing | none |
| `BadRequestError` | HTTP | response status 400 | none |
| `NotFoundError` | HTTP | response status 404 | none |
| `RouteNotFoundError` | HTTP | routing | none |
| `MethodNotAllowedError` | HTTP | routing | none |
| `NotAcceptableError` | HTTP | content negotiation | none |
| `ConflictError` | HTTP | response status 409 | none |
| `GoneError` | HTTP | response status 410 | none |
| `PreconditionFailedError` | HTTP | response status 412 | none |
| `RequestBodyTooLarge` | HTTP | request body limit | none |
| `UnsupportedMediaTypeError` | HTTP | content negotiation | none |
| `TooManyRequestsError` | HTTP | rate limiting | none |
| `ServiceUnavailableError` | HTTP | response status 503 | none |
| `RequestTimeoutError` | HTTP | response status 504 | none |
| `UnauthorizedError` | HTTP | response status 401 | none |
| `ForbiddenError` | HTTP | response status 403 | none |
| `MissingStateError` | HTTP | request state object | `LifecycleViolationError` for scope state (PRD 03) |
| `StateTypeError` | HTTP | request state object | none |
| `RouterConflictError` | routing | no router | none |
| `CircularModuleError` | modules | no module graph; provider cycles are `CircularDependencyError` | `CircularDependencyError` |
| `ModuleExportViolation` | modules | visibility is a filter, not an export system (PRD 06) | `MissingProviderError` with `module` (PRD 06) |
| `MiddlewareConfigError` | ASGI | no middleware | none |
| `GuardConfigError` | ASGI | no guards | none |
| `InterceptorConfigError` | ASGI | no interceptors | none |
| `ExceptionHandlerConfigError` | ASGI | no exception handlers | none |
| `SessionConfigError` | config | no sessions | none |
| `ConfigValidationError` | config | no settings loader; a config value is a `use_value` row | `InvalidProviderError` for a malformed `use_value` |
| `OpenAPISchemaError` | OpenAPI | no schema generation | none |
| `DestructTimeoutError` | lifecycle | no per-hook timeout (PRD 03 L-48) | none — bound the call with `asyncio.wait_for` |
| `DrainTimeoutError` | lifecycle | reserved in the reference, never raised | none |

Four classes in that table need a sentence of justification rather than a row:

- **`ModuleExportViolation`** is the closest call. It is tempting to keep it for PRD 06, but the
  visibility model there is a *filter*: a token you cannot see behaves exactly like a token nobody
  registered, and reporting it as a different class would leak the module graph into every handler.
- **`CircularModuleError`** is the second closest: a module cycle is a real thing in `lauren`, and it
  is not a thing in a container that has no modules.
- **`DestructTimeoutError`** is dropped per D-21 (no per-hook timeout), not because it is HTTP-bound.
- **`ConfigValidationError`** is dropped because typed configuration is a framework feature (PRD 01
  §3); a value the user pre-validates is a `use_value` row and cannot be invalid by the time it is
  registered.

Not one of these is imported, referenced or named by `lauren_di.__all__`, and no leaf in §5.6 mentions
HTTP, routing, modules, extractors or scheduling in its message. T05-50 asserts the negative.

### 5.9 Five failures, end to end

```python fragment
from lauren_di import DIContainer, DIError, Scope

# 1. Cycle
#    CircularDependencyError: Circular dependency: Repo -> Db -> Repo
#    detail == {"cycle": ["Repo", "Db", "Repo"]}

# 2. Missing provider, named at the edge
#    MissingProviderError: No provider for Mailer (required by Report.mailer)
#    detail == {"token": "Mailer", "dependent": "Report", "param": "mailer",
#               "hint": "register Mailer, or give the parameter a default"}

# 3. Ambiguity, declaration form
#    ProtocolAmbiguityError: Multiple bindings for Repo (2)
#    detail == {"token": "Repo", "count": 2, "hint": "register one provider, or depend on list[Repo]"}

# 4. Scope narrowing
#    DIScopeViolationError: Singleton Report depends on request-scoped Ctx
#    detail == {"dependent": "Report", "dependency": "Ctx",
#               "dependent_scope": "singleton", "dependency_scope": "request"}

# 5. Dangling alias
#    MissingProviderError: Alias Legacy -> Logger points at an unknown token
#    detail == {"alias": "Legacy", "target": "Logger",
#               "hint": "register Logger, then the alias resolves through it"}

try:
    container.compile()
except DIError as exc:
    log.error("%s [%s] %s", exc.message, exc.code, exc.detail)
```

The five messages are the whole diagnostic surface for graph problems. Each names the token involved,
and four of the five name the edge as well — which is the difference between "something is missing"
and "this line is wrong" (G3, D-34).

### 5.10 A complete validation program

```python run
import asyncio

from lauren_di import (
    DIContainer, DIError, MissingProviderError, Scope, injectable,
)

@injectable()
class Clock:
    def now(self) -> str:
        return "2026-11-21T00:00:00Z"

@injectable(scope=Scope.REQUEST)
class RequestContext:
    pass

@injectable()
class Report:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

async def main() -> None:
    container = DIContainer()
    container.register(Clock)
    container.register(Report)
    container.compile()                     # clean: two providers, one edge

    # A scope violation is a compile error, not a runtime surprise.
    broken = DIContainer()
    broken.register(Clock)
    broken.register(RequestContext)

    @injectable()
    class NeedsContext:
        def __init__(self, ctx: "RoundTripContext") -> None: ...

    broken.register(NeedsContext)
    try:
        broken.compile()
    except MissingProviderError as exc:
        assert exc.code == "missing_provider"
        assert exc.detail["token"] == "RoundTripContext"
        assert exc.detail["dependent"] == "NeedsContext"
        assert exc.detail["param"] == "ctx"

    # Registration after a successful compile invalidates it.
    container.register(RequestContext)
    container.compile()
    assert await container.resolve(Report) is not None

asyncio.run(main())
```

Three details are the contract: `compile()` is synchronous, so it is called without `await`; the
missing-provider error names the *edge*, not just the absent token; and a second `compile()` after
`register` revalidates rather than trusting the old result.

## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

Three new modules, one changed call site. The pipeline is a free function over the container's
registry, not another container method, so it can be unit-tested with a hand-built provider list.

| Reference symbol | Line | Becomes | Change |
|------------------|------|---------|--------|
| `DIContainer.compile` | 978 | `_container.compile()` → `_validate.validate(self)` | **Rewritten** (D-31, D-32): six named phases instead of one interleaved DFS |
| `_check_scope` (closure) | 986 | `_validate._check_scope` | Same comparison (`for_scope > chosen.scope`); message and detail per row 20 |
| `resolve_one` (closure) | 1012 | `_validate._edges_for` | Split: this PRD needs the *edge list*, not a resolved provider, so the alias and `list[T]` expansions move to `_edges_for` |
| `visit` (closure) | 1122 | `_validate._walk` | **Split** into phases 3–5; one walk produces the edge set, three passes over it raise the three classes |
| framework-token skip | 1120 | — | **Removed**: there are no framework tokens (PRD 01 §3) |
| eager bindings sweep | 1193 | `_validate._phase_ambiguity` | Moved to **phase 1** (D-31) and given `token` + `hint` |
| `self.instance_index()` / `self._compiled = True` | 1208 | `_validate._finalise` | Unchanged in effect |
| `_get_injectable_meta` | 1585 | `_meta.get_meta` | Raises `MetadataInheritanceError` with `target` + `base` (row 25) |
| `_inspect_callable_deps` | 2013 | `_introspect.callable_deps` | Adds the positional-only rejection (D-19) and `hint` |
| `_inspect_class_deps` | 1813 | `_introspect.class_deps` | Adds the bare-`Token` field rejection (D-30) |
| `_describe` | 1805 | `_render.describe_token` | **Rewritten** (D-33): generic parameters and quotes preserved |
| `_validate_token` (`custom.py`) | 270 | `_errors.validate_token` | Same two checks; message and detail per row 4 |
| `lauren/exceptions.py` | 101–722 | `_errors.py` | **Rewritten**: `DIError` root, 13 flat leaves, no `status_code` / `headers` / `to_payload()` |

Two consequences worth calling out:

- **`_walk` no longer raises.** The reference's walk raises as it recurses, which is what makes the
  surfaced error order-dependent (§1.2). Producing the edge set first and validating it afterwards is
  what buys G2, and it costs one extra pass over an already-materialised list.
- **`_errors.py` is import-free.** It imports nothing from the package, so `DIError` can be imported
  from a traceback in a REPL without pulling in the container, and PRD 07's overrides can raise the
  same leaves without a cycle.

### 6.2 Parity table

Rule IDs are stable and are cited by PRD 06 (visibility), 07 (overrides) and 08 (migration).
"Observed" records what `../lauren-framework` did on 2026-11-21, probed rather than read.

| Rule | Normative statement | Reference symbol | Observed reference behaviour | Test |
|------|--------------------|------------------|------------------------------|------|
| V-01 | `compile() -> None` is synchronous; no `compile_async()` exists | `compile` (978) | `def compile(self) -> None`, sync | T05-01 |
| V-02 | A second `compile()` with no registration between is a no-op | `if self._compiled: return` (980) | returns immediately | T05-02 |
| V-03 | A failed compile leaves the container uncompiled, so the next call retries | `_compiled` set last | flag stayed `False` after a raised cycle (E-120) | T05-03 |
| V-04 | Any `register*` after a successful compile clears the flag (D-15) | `if self._compiled: return` | **diverges**: flag stayed `True`; the new provider was never validated (E-110) | T05-04 |
| V-05 | The first `resolve()` validates (D-14) | `resolve` never calls `compile` | **diverges**: a cycle resolved to `RecursionError` | T05-05 |
| V-06 | `compile()` constructs nothing and calls no user code | walk reads `deps` only | zero constructions recorded during a clean compile (E-124) | T05-06 |
| V-07 | Registry reads (`get_provider`, `has_provider`, `registered_providers`, `all_providers`, `instance_index`) never compile | `has_provider` (1219) | `has_provider` returned `True` and left `_compiled` `False` (E-116) | T05-07 |
| V-08 | Phase order: ambiguity (1) is reported before cycles (3) | sweep 1193, DFS 1122 | **diverges**: the DFS ran first, so a cycle won regardless of registration order (E-108) | T05-08 |
| V-09 | Phase order: a dangling alias (2) is reported before cycles (3) | alias branch 1140 | **diverges**: same single DFS, so the winner depended on which provider was reached first | T05-09 |
| V-10 | Phase order: cycles (3) are reported before missing providers (4) | one DFS raises both | interleaved; a cycle nearer the entry point won | T05-10 |
| V-11 | Phase order: missing providers (4) are reported before scope violations (5) | one DFS raises both | interleaved; whichever edge came first won | T05-11 |
| V-12 | Fail-fast: exactly one error per compile, identical across four registration orders | one raise, no collection | one error, but a *different* one per order (E-108) | T05-12 |
| V-13 | No aggregate error type exists | — | — | T05-13 |
| V-14 | A token with >1 non-`multi` binding fails validation even when nothing depends on it | eager sweep 1193 | `ProtocolAmbiguityError {"token","count"}` (E-107) | T05-14 |
| V-15 | The same condition reached through an edge fails with the same class | `resolve_one` 1106 | raised during the DFS (E-117) | T05-15 |
| V-16 | Ambiguity detail always carries `token`, `count` and a `hint`; the edge form adds `dependent`, `param` | sweep / DFS | **diverges**: `{"token","count"}` at the sweep, `{"count"}` at the edge, `{}` from `get_provider` (E-107, E-117, E-127) | T05-16 |
| V-17 | A token bound by both `multi` and non-`multi` providers is ambiguous | `not all(b.multi …)` | raised | T05-17 |
| V-18 | Two or more `multi` bindings alone are not ambiguous | same check | no error; `resolve(T)` returned a list (E-107 context, PRD 04 M-46) | T05-18 |
| V-19 | An unknown alias target fails at validation with `alias` and `target` | alias branch 1140 | `MissingProviderError {"alias","target"}` (E-114) | T05-19 |
| V-20 | An alias chain is validated to its terminal provider; a broken link is reported once | `visit(target_provider)` | chain walked; one error | T05-20 |
| V-21 | An alias cycle is a `CircularDependencyError` with `cycle` | GRAY path 1127 | `{"cycle": ["A","B","A"]}` | T05-21 |
| V-22 | Alias edges participate in provider cycle detection | same | raised | T05-22 |
| V-23 | A provider cycle is a `CircularDependencyError` with `cycle` | 1127 | `{"cycle": ["C1","C2","C1"]}` (E-102) | T05-23 |
| V-24 | A self-cycle renders as the token twice | 1127 | `{"cycle": ["S","S"]}` (E-103) | T05-24 |
| V-25 | The reported path starts at the first repeated token, not the entry provider | `stack[stack.index(tok):]` | `Entry -> L1 -> L2 -> L1` reported `["L1","L2","L1"]` (E-118) | T05-25 |
| V-26 | Each `list[T]` member is an independent edge | `for dep_provider in dep_providers` | cycle through a member reported `["M1","Agg","M1"]` (E-121) | T05-26 |
| V-27 | An `OptionalDep` edge participates when the provider exists | `optional_dep_names` skip only on failure | the edge was walked | T05-27 |
| V-28 | `cycle` is an ordered, closed list of rendered tokens | `[_describe(t) for t in …]` | closed on the repeating token | T05-28 |
| V-29 | A missing edge reports `token`, `dependent`, `param`, `hint` | `MissingProviderError(...)` 1087 | **diverges**: `{"token"}` only — no consumer named (E-104) | T05-29 |
| V-30 | A parameter default suppresses the missing-provider error | `defaults.get(name, False)` | no error (E-106) | T05-30 |
| V-31 | An `OptionalDep` member suppresses it | `optional_dep_names` | no error, factory receives `None` | T05-31 |
| V-32 | Edges of an unreferenced provider are still checked | `for p in self._providers.values()` | missing dep of an unregistered consumer raised (E-105) | T05-32 |
| V-33 | A leftover string annotation is a real token and is never re-resolved | `_extract_inject_token` | `{"token": "not_a_real_token"}`; registering the class later did not fix it (E-122) | T05-33 |
| V-34 | `list[T]` with no provider reports the rendered collection and the element | 1033 | `{"token": "list"}` — the element type was lost (E-101) | T05-34 |
| V-35 | A consumer that outlives its dependency is a `DIScopeViolationError` with four keys | `_check_scope` 986 | `{"dependent_scope","dependency_scope","dependency"}` — no `dependent` (E-109) | T05-35 |
| V-36 | Equal scopes and narrower consumers are legal | `for_scope > chosen.scope` | allowed for all non-widening pairs | T05-36 |
| V-37 | The check covers constructor, function-provider, class-body and `list[T]` edges | `deps` + `field_deps`, per member | all four checked | T05-37 |
| V-38 | The runtime strict-request case is the same class with `token` and `reason` | `context.py` raise | same class, framework shape | T05-38 |
| V-39 | `MetadataInheritanceError` is raised by `register` / `injectable_meta`, naming both classes | `_get_injectable_meta` 1585 | raised at `register` with an empty detail (E-111) | T05-39 |
| V-40 | `UnresolvableParameterError` covers three registration-time triggers with `target`, `param`, `hint` | `_inspect_callable_deps` 2001 | missing annotation raised with `{"target","param"}`; positional-only **accepted** (E-112, E-113) | T05-40 |
| V-41 | `InvalidProviderError` rejects a non-class, non-callable target | no check | **diverges**: a broken function provider was installed (D-16) | T05-41 |
| V-42 | A duplicate non-`multi` token is a registration error; two `provides` bindings survive to phase 1 | `DuplicateBindingError` 364, 770 | duplicate token raised; `provides` collision reached the sweep (E-107) | T05-42 |
| V-43 | `DecoratorUsageError` covers bare decorator, bad token, bad `use`/`factory`, self-alias | `_validate_token`, helpers | all four raised `DecoratorUsageError` with an empty detail (E-101 context, PRD 04 probes) | T05-43 |
| V-44 | `LifecycleConfigError` covers a transient generator and a hook on a non-callable | `_scheduling` / `decorators` | transient generator raised the **base** `StartupError` (E-123) | T05-44 |
| V-45 | Every leaf subclasses `DIError`; there is no `StartupError` layer | `StartupError` 140 | leaves sit under `StartupError` or `LifecycleError` | T05-45 |
| V-46 | `code` is stable per leaf, `str()` returns `message`, `detail` is a dict, no trailing period | `LaurenError.code` 105 | codes match the leaf names in snake_case | T05-46 |
| V-47 | `detail` is JSON-serialisable everywhere | mixed: strings, ints, `frozenset` keys… | `json.dumps` fails on nothing observed, but keys are inconsistent | T05-47 |
| V-48 | `DestructError` is returned by teardown and never raised | raised? no — collected in `_lifecycle` | collected and returned | T05-48 |
| V-49 | `compile()` never raises a declaration error (D-36) | metadata read at register | `MetadataInheritanceError` and `UnresolvableParameterError` both surfaced at `register` (E-111, E-112) | T05-49 |
| V-50 | No framework-only class is exported, imported or named in a message | 46 classes in `exceptions.py` | 35 of them are framework-only | T05-50 |

Rules V-04, V-05, V-08 … V-11, V-16, V-29, V-34, V-35, V-41 and V-44 are the divergences; the rest
are parity with a repaired `detail`. V-30, V-31 and V-32 are parity that is easy to lose in a rewrite
and therefore has its own test.

### 6.3 Divergences

| # | Decision | Reference | `lauren-di` | Why | ID |
|---|----------|-----------|-------------|-----|-----|
| 1 | Which error a broken graph reports | interleaved DFS + trailing sweep; winner depends on insertion order | fixed six-phase pipeline, fail-fast, documented order | a catalogue cannot describe behaviour that changes with registration order; "declare, link, topologise, complete, bound" is also the order a user fixes things | D-31 |
| 2 | Is validation async | sync `compile`, async `resolve` | sync `compile`, sync pipeline called by `resolve` | validation reads records; making it async would suggest it can construct, which it must not | D-32 |
| 3 | How a token is rendered | `__name__` or `str()`; loses generic args and quotes (`list`, `SETTINGS`, `Token(DB)`) | `Plugin`, `"SETTINGS"`, `Token('DB', unique=False)`, `list[Plugin]` | two different tokens must never print identically; `detail` is consumed programmatically | D-33 |
| 4 | Does a validation error name the edge | no: `{"token"}` or `{"count"}` | yes: `dependent` + `param` added whenever an edge found it | "No provider for Mailer" costs a search; "required by Report.mailer" is the fix | D-34 |
| 5 | Is `DestructError` raised or returned | collected by `_lifecycle`, returned by shutdown | returned, never raised; still a `DIError` subclass | a teardown failure must not abort teardown (PRD 03 D-20), but callers still want `except DIError` | D-35 |
| 6 | Where declaration errors are raised | at `register` / `injectable_meta` (same) | at `register` / `injectable_meta` (same), and **never** at `compile` | parity kept deliberately: a provider that cannot be introspected must not enter the registry, so `compile` needs no phase for it | D-36 |

### 6.4 What validation does not check

An explicit list, because "validated" is otherwise read as "everything works":

| Not checked | Consequence | Where it is caught |
|-------------|-------------|--------------------|
| Whether a constructor or `__init__` body succeeds | a raising constructor fails at `resolve()` | PRD 02 |
| Whether a factory is async, blocking or slow | any callable is accepted | PRD 04 |
| Whether a hook is idempotent, or what it does | hooks run at the lifecycle points | PRD 03 |
| Whether a `use_value` payload is the right type | any object is a legal value | PRD 04 |
| Visibility beyond the pre-computed filter (PRD 06) | a token invisible to a module reports `MissingProviderError` | PRD 06 |
| Overrides that never matched anything | accepted silently unless the plan is built with `strict=True` | PRD 07 |
| Per-request lifetimes of instances | only scopes, never instances | PRD 03 |
| Anything about HTTP, routes, extractors, modules or scheduling | not part of the library | PRD 01 §3 |

The boundary matters for expectations: a green `compile()` means *the graph is well-formed and every
edge is satisfiable at a legal lifetime*. It does not mean the program works.

### 6.5 Cost

| Phase | Work | Bound |
|-------|------|-------|
| 1 | one pass over `registered_providers()` | O(P) |
| 2 | one pass over alias rows, each chain followed once | O(A + chain length) ≤ O(P) |
| 3–5 | materialise the edge set once, then three linear passes | O(P + E) |
| 6 | precompute the instance index | O(P) |

`E` is the number of dependency edges. Nothing is quadratic, nothing recurses unboundedly (the walk is
iterative after the edge list exists, so a deep graph cannot hit the recursion limit the way the
reference's `visit` can), and nothing allocates per token beyond one dict entry and one list entry per
provider. A 1 000-provider graph is a few hundred microseconds; the number that matters is that the
first `resolve()` pays it once and registration invalidates it only when something changed.

## 7. Error Catalog Impact

This PRD **is** the catalogue, so the impact table is a reconciliation: what PRDs 01–04 already
promised, and where this document agrees, tightens or amends it.

| Source | Promise | Status here |
|--------|---------|-------------|
| PRD 01 §7 | `DIError` root with `code`, `message`, `detail`; `__str__` returns `message` | Confirmed, §5.5 |
| PRD 01 §7 | `InvalidProviderError` is a new leaf (`code="invalid_provider"`) | Confirmed, row 2 |
| PRD 01 §7 | `LaurenError.status_code` / `.headers` / `.to_payload()` dropped | Confirmed: `DIError` has no HTTP surface |
| PRD 01 §7 | `StartupError` not exported | Confirmed, and **not carried at all** — the hierarchy is flat (§5.6.1) |
| PRD 01 §7 | "Unchanged in meaning": ten named leaves | Confirmed for all ten; codes, triggers and `detail` fixed in §5.6 |
| PRD 01 §7 | "Not carried": 13 named classes | Confirmed for 12; the thirteenth, `LifecycleViolationError`, moved to a "Carried after review" row in PRD 01 per D-23 (PRD 03 §5.6) and appears here as row 28 |
| PRD 01 §7 | "no leaf is named `…Error` unless it is raised, never returned" | **Amended**: `DestructError` is returned by teardown and is still a leaf (D-35) |
| PRD 02 §6.2 | `UnresolvableParameterError` carries `target`, `param` | Confirmed, plus `hint` (row 22–24, D-36) |
| PRD 02 §6.2 | `LifecycleConfigError` carries `provider` | Confirmed, plus `hint` (row 26) |
| PRD 03 §6.3 | `DIScopeViolationError` has two shapes, four keys and two keys | Confirmed, rows 20–21 |
| PRD 03 §6.3 | `LifecycleViolationError` carries `state` | Confirmed, row 28 |
| PRD 03 §6.3 | `DestructError` carries `class`, `stage`, `origin`, `cause` | Confirmed, row 29 |
| PRD 04 §7 | ambiguity/duplicate `detail` keys; `{"parameter","token","hint"}` for `UnresolvableParameterError` | **Amended**: the keys are `target`, `param`, `hint` — the shape PRD 02 §6.2 and the reference already use (row 22) |

Two amendments touch documents that are already verified. Both are recorded as decisions (D-35 covers
the `DestructError` wording; the `UnresolvableParameterError` key set is fixed here, with PRD 02 as the
authority it agrees with), and both are one-line edits in the affected tables so the goal #10 pass can
verify them mechanically.

Nothing is added to `lauren_di.__all__` beyond the 14 leaves. The renderer in §5.7 is private: it is
observable through `message` and `detail`, and exporting it would freeze a formatting contract that
belongs with the messages.

## 8. Phased Implementation Plan

| Phase | Version | Work | Exit criterion |
|-------|---------|------|----------------|
| 1 | 0.1.0 | `_errors.py`: `DIError` + 14 leaves, `code` attributes, `detail` defaulting, `__str__` | every leaf imports from `lauren_di`; `json.dumps` on every `detail` in the test suite |
| 2 | 0.1.0 | `_render.describe_token` and its tests, including `Token` and `list[T]` | the five renderings in §5.7 rule 1 asserted |
| 3 | 0.1.0 | `_validate`: edge extraction (`_edges_for`) and phases 1–2 | T05-08, T05-14 … T05-21 green |
| 4 | 0.1.0 | phases 3–6: cycle walk, completeness, scope narrowing, finalise | T05-23 … T05-38 green |
| 5 | 0.1.0 | wire `compile()` and the lazy first-resolve path; invalidation on `register*` | T05-01 … T05-07 green |
| 6 | 0.1.0 | re-base every raise site in PRDs 02–04 onto the new leaves and shapes | T05-39 … T05-49 green; no `StartupError` remains in the tree |
| 7 | 0.1.1 | message/detail polish, `hint` coverage audit | T05-50 green; the §5.8 table asserted by an import-blocking test |

Phase 6 is the risky one: it touches every `raise` in the package. It is deliberately after the
pipeline works, so the new tests can prove the shapes before the old sites are rewritten.

## 9. Documentation & Convention Updates

| Document | Change |
|----------|--------|
| `README.md` | a "Validation" section: first `resolve()` validates, `compile()` is explicit and idempotent, one error at a time |
| `docs/errors.md` (new) | the §5.6 catalogue as the canonical reference, one anchor per `code`, generated shape documented |
| `docs/validation.md` (new) | the six phases, the ordering guarantee, and the §6.4 "not checked" list |
| `docs/migration.md` (PRD 08) | the §5.8 framework-only table as the "is `X` supported?" answer |
| `llms.txt` / `llms-full.txt` | every leaf with its code and one-line trigger; the ordering guarantee stated once |
| `CHANGELOG.md` | `0.1.0` lists the 14 leaves and the six validation phases as the initial surface |
| Contribution guide | **how to add an error**: a new leaf needs a code, a trigger row, a `detail` shape, a JSON-serialisability test and a `hint`; a new *shape* for an existing leaf needs a new catalogue row and a test that distinguishes it |

Convention worth writing down in the guide: **one leaf, many triggers, never many classes.** A new
trigger for an existing mistake gets a row in §5.6.2, not a subclass; a genuinely different mistake
gets a leaf. The reference's 46 classes are the counter-example — 35 of them are one family that does
not exist here.

## 10. Test Plan

Tests live in `tests/unit/validation/`, `tests/unit/errors/` and `tests/parity/`. Everything below uses
public names only: `lauren_di` imports plus `pytest.raises`. `detail` and `code` are asserted directly;
`message` is asserted by shape (substring), never by full equality, except where this PRD fixes the
wording.

### 10.1 Enumerated test cases

| ID | Test name | Setup | Assertion |
|----|-----------|-------|-----------|
| T05-01 | `test_compile_is_sync_and_returns_none` | clean container | `container.compile() is None`; not awaitable (`inspect.iscoroutinefunction` false) |
| T05-02 | `test_compile_twice_is_noop` | clean container, compile twice | second call returns; no error; graph unchanged |
| T05-03 | `test_failed_compile_can_be_retried` | cycle, compile raises; fix, compile | second compile clean |
| T05-04 | `test_register_after_compile_invalidates` | compile clean, `register` a broken provider, compile | raises the broken provider's error (reference: no error) |
| T05-05 | `test_first_resolve_compiles` | cycle, `await resolve(...)` | `CircularDependencyError`, not `RecursionError` |
| T05-06 | `test_compile_constructs_nothing` | provider whose `__init__` appends to a list | list empty after `compile()` |
| T05-07 | `test_lookups_do_not_compile` | cycle; `get_provider`, `has_provider`, `registered_providers`, `instance_index`, `singletons` | none raises; then `compile()` raises |
| T05-08 | `test_ambiguity_reported_before_cycle` | cycle **and** two non-multi bindings; both orders | `ProtocolAmbiguityError` both times |
| T05-09 | `test_alias_target_reported_before_cycle` | dangling alias **and** cycle | `MissingProviderError` with `alias` |
| T05-10 | `test_cycle_reported_before_missing` | cycle **and** unrelated missing dep | `CircularDependencyError` |
| T05-11 | `test_missing_reported_before_scope` | missing dep **and** scope violation | `MissingProviderError` |
| T05-12 | `test_error_is_identical_across_four_orders` | same broken graph, four registration permutations | same class and same `detail` four times |
| T05-13 | `test_no_aggregate_error_type` | broken graph | `lauren_di` exports no type whose name contains `Aggregate`; exactly one error raised |
| T05-14 | `test_unreferenced_ambiguity` | two `provides=[T]` providers, no consumer | `ProtocolAmbiguityError`, `detail["token"]`, `count == 2`, `hint` present |
| T05-15 | `test_edge_ambiguity` | one consumer depending on `T`, two non-multi bindings | same class; `dependent` and `param` present |
| T05-16 | `test_ambiguity_detail_shapes` | sweep, edge and `resolve` forms | each has `token`+`count`; edge adds `dependent`/`param`; resolve adds `hint` |
| T05-17 | `test_mixed_multi_and_non_multi` | one `multi=True` and one plain binding | `ProtocolAmbiguityError` |
| T05-18 | `test_two_multi_bindings_are_fine` | two `multi=True` bindings | `compile()` clean; `resolve(list[T])` returns two |
| T05-19 | `test_alias_target_unknown` | `use_existing("A", "MISSING")` | `MissingProviderError`, `detail == {"alias","target","hint"}` |
| T05-20 | `test_alias_chain_validated_to_terminal` | `A -> B -> C`, `C` absent | one `MissingProviderError` naming `C` |
| T05-21 | `test_alias_cycle` | `A -> B -> A` | `CircularDependencyError`, `detail["cycle"] == ["A","B","A"]` |
| T05-22 | `test_alias_into_provider_cycle` | `A -> B`, `B` depends on `A` | `CircularDependencyError` |
| T05-23 | `test_provider_cycle` | `C1 <-> C2` | `cycle == ["C1","C2","C1"]` |
| T05-24 | `test_self_cycle` | `S` depends on `S` | `cycle == ["S","S"]` |
| T05-25 | `test_cycle_path_excludes_entry` | `Entry -> L1 -> L2 -> L1` | `cycle == ["L1","L2","L1"]` |
| T05-26 | `test_cycle_through_collection_member` | `M1` (multi) depends on `Agg`, `Agg` depends `list[Leaf]` | `cycle == ["M1","Agg","M1"]` |
| T05-27 | `test_optional_dep_edge_is_walked` | `use_factory(injects=[OptionalDep(Dep)])`, `Dep` depends back | `CircularDependencyError` |
| T05-28 | `test_cycle_detail_is_ordered_and_closed` | three-token cycle | list of rendered strings; `[0] == [-1]`; length == cycle length + 1 |
| T05-29 | `test_missing_edge_names_consumer` | `Report.__init__(mailer: Mailer)` | `detail == {"token","dependent","param","hint"}`, `dependent == "Report"` |
| T05-30 | `test_parameter_default_suppresses` | `def __init__(self, d: Missing = None)` | `compile()` clean |
| T05-31 | `test_optional_dep_suppresses` | `use_factory(injects=[OptionalDep(Missing)])` | `compile()` clean; factory receives `None` |
| T05-32 | `test_unreferenced_provider_edges_checked` | one provider with a missing dep, no consumer | `MissingProviderError` |
| T05-33 | `test_string_token_is_not_re_resolved` | `d: "Later"`, register `Later` afterwards | still `MissingProviderError` with `token == "Later"` |
| T05-34 | `test_collection_without_providers` | `xs: list[Plugin]`, none registered | `token == "list[Plugin]"`, `element == "Plugin"` |
| T05-35 | `test_scope_violation_shape` | singleton → request | four keys; `dependent_scope == "singleton"` |
| T05-36 | `test_scope_matrix_allow_cases` | equal and narrowing pairs | `compile()` clean for all |
| T05-37 | `test_scope_checked_on_every_edge_kind` | constructor, function provider, field, `list[T]` | each raises `DIScopeViolationError` |
| T05-38 | `test_runtime_strict_request_shape` | request-scoped, no scope, strict | `{"token","reason"}`, `reason == "no_active_scope"` |
| T05-39 | `test_metadata_inheritance` | undecorated subclass of a decorated class | `register` and `injectable_meta` both raise; `detail == {"target","base"}` |
| T05-40 | `test_unresolvable_parameter_triggers` | missing annotation, positional-only, bare `Token` field | three raises; `{"target","param","hint"}` each |
| T05-41 | `test_invalid_provider` | `register(object())` | `InvalidProviderError`, `{"target","kind"}` |
| T05-42 | `test_duplicate_versus_provides_collision` | same token twice; two `provides` bindings | `DuplicateBindingError` at register; `ProtocolAmbiguityError` at compile |
| T05-43 | `test_decorator_usage_triggers` | bare `@injectable`, `None` token, non-class `use`, self-alias | four `DecoratorUsageError`s with the documented keys |
| T05-44 | `test_lifecycle_config_triggers` | transient generator; hook on a non-callable | two `LifecycleConfigError`s |
| T05-45 | `test_all_leaves_are_dierror` | every exported leaf | `issubclass(leaf, DIError)`; no `StartupError` attribute |
| T05-46 | `test_code_and_message_contract` | one error per leaf | code matches the table; `str(exc) == exc.message`; no `"\n"`, no trailing `"."` |
| T05-47 | `test_detail_is_json_serialisable` | every raised error in the suite, collected by a fixture | `json.dumps(exc.detail)` succeeds; no `type`/`set`/`Token` values |
| T05-48 | `test_destruct_error_is_returned` | hook raises at teardown | `close()` returns a tuple; nothing raised; the value is a `DIError` |
| T05-49 | `test_compile_raises_no_declaration_error` | undecorated subclass, unannotated param, bad target | each raised by `register`, never reachable from `compile()` |
| T05-50 | `test_no_framework_only_class` | import `lauren_di`, inspect `__all__` and every message | none of the 35 names in §5.8 appears |

### 10.2 Parity tests

```python fragment
import pytest


@pytest.mark.parity
def test_phase_order_is_stable() -> None:
    from lauren_di import DIContainer

    container = DIContainer()
    # ... cycle plus two providers bound to one token, registered in the worst order
    with pytest.raises(Exception) as info:
        container.compile()
    assert info.value.code == "protocol_ambiguity"   # reference: circular_dependency


@pytest.mark.parity
@pytest.mark.xfail(strict=True, reason="D-15: registration after compile invalidates the graph")
def test_revalidation_after_late_registration() -> None:
    ...


@pytest.mark.parity
@pytest.mark.xfail(strict=True, reason="D-34: validation errors name the consumer edge")
def test_missing_provider_names_the_edge() -> None:
    ...


@pytest.mark.parity
@pytest.mark.xfail(strict=True, reason="D-33: rendered tokens keep quotes and generic parameters")
def test_collection_token_rendering() -> None:
    ...
```

Rules V-04, V-05, V-08 … V-11, V-16, V-29, V-34, V-35, V-41 and V-44 carry `xfail(strict=True)` with
the decision ID in the reason, so removing a divergence flips the test and forces this PRD to change.
The remaining rules run unchanged against both implementations.

### 10.3 Acceptance traceability for this PRD

| Acceptance clause | Where satisfied |
|-------------------|-----------------|
| **`compile()` phases: cycle detection, missing providers, ambiguity, scope violations, alias target existence** | §5.2 (six-phase table), §6.3 row 1 (D-31), V-08 … V-11, T05-08 … T05-11, T05-14 … T05-37 |
| **… unresolved parameters, inherited-metadata rejection** | §5.2 closing paragraph and D-36: both are registration-time, so `compile()` has no phase for them; §5.3 timeline; V-39, V-40, V-49; T05-39, T05-40, T05-49 |
| **When validation runs relative to registration** | §5.1 rules 1–7, §5.3 (timeline and table), V-01 … V-07, T05-01 … T05-07 |
| **A complete error catalogue as a table with exception class, hierarchy under one `DIError` base, trigger, message shape, `detail` keys, remediation** | §5.6.1 hierarchy, §5.6.2 33 rows, §5.6.3 per-leaf notes, §5.7 conventions (`hint` is the remediation field), T05-45 … T05-47; rows 30–32 by T06-16, T06-17 and T06-03 |
| **Every exception named in the clarification evidence is catalogued or marked framework-only** | 14 leaves + root in §5.6.2; all 35 remaining classes of `lauren/exceptions.py` in §5.8, with reason and replacement; T05-50 asserts the negative |
| **Each entry names the public call that raises it and the test that asserts it** | `Raised by` and `Test` columns of §5.6.2; every call is a public name (`compile`, `resolve`, `register`, `register_*`, `use_*`, the decorators, `ResolutionScope.*`, `close`, `shutdown`) |
