# lauren-di — PRD Set

**Status:** Draft
**Date:** 2026-11-21
**Product intent:** `lauren-di` is a standalone Python library that does **only**
dependency injection. It has no HTTP layer, no router, no configuration loader,
no scheduler and no CLI.

This directory holds the specification of that library. The PRDs are written
against a studied reference implementation — the DI subsystem of
`../lauren-framework` (`lauren/_di/`) — and specify how its behaviour becomes a
library with a smaller public surface and no framework attachments.

---

## 1. How to read this set

The PRDs are **numbered in dependency order**: a document may only rely on
decisions made in a lower-numbered document. Each follows the same template, so
a reviewer can diff them section-by-section:

1. Problem Statement
2. Goals
3. Non-Goals
4. Prior Art
5. Proposed API
6. Architecture Fit & Feasibility
7. Error Catalog Impact
8. Phased Implementation Plan
9. Documentation & Convention Updates
10. Test Plan

Two conventions are load-bearing:

- **Decisions have IDs.** Anything that could plausibly be decided the other way
  gets a `D-n` entry in the register in §4 and is cited by ID wherever it is
  used. A PRD does not re-litigate a decision made earlier; it cites it.
- **Scope is testable.** Every PRD states a Non-Goals section. The product-level
  test is simple: if a proposed feature needs an HTTP request object, a route
  table, an extractor, a config file or a module graph compiler, it does not
  belong in `lauren-di`.

## 2. The set

| # | File | Concern | Depends on | Status |
|---|------|---------|------------|--------|
| 01 | [`01-product-definition-and-public-api.md`](01-product-definition-and-public-api.md) | Product boundary, distribution and import names, supported Python versions, dependency policy, the complete exported-symbol table and quickstart | — | Draft — 2026-11-21 |
| 02 | [`02-core-container-and-provider-registration.md`](02-core-container-and-provider-registration.md) | `@injectable` semantics, `DIContainer.register`, the `Provider` record, constructor and class-body field injection, function and generator providers, annotation introspection, zero-config default path | 01 | Draft — 2026-11-21 |
| 03 | [`03-scopes-lifecycle-and-disposal.md`](03-scopes-lifecycle-and-disposal.md) | `Scope` ordering and narrowing, strict vs lax request-scope resolution, the scope context manager that owns the request cache, `@post_construct` / `@pre_destruct` ordering, teardown and `DestructError` containment | 01, 02 | Draft — 2026-11-21 |
| 04 | [`04-custom-providers-tokens-and-multi-bindings.md`](04-custom-providers-tokens-and-multi-bindings.md) | `Token`, `Inject`, `OptionalDep`, `CustomProvider`, the four `use_*` recipes, alias chains, `multi=True` and `list[T]` collection resolution | 01, 02 | Draft — 2026-11-21 |
| 05 | [`05-compile-time-validation-and-errors.md`](05-compile-time-validation-and-errors.md) | `compile()` phases, when validation runs, and the complete error catalogue under a single `DIError` base | 02, 03, 04 | Draft — 2026-11-21 |
| 06 | [`06-module-visibility-decision-record.md`](06-module-visibility-decision-record.md) | Whether NestJS-style module encapsulation ships in v1 or v2, plus the design of `owning_module`, `set_visible` and visibility-filtered resolution either way | 02, 05 | Draft — 2026-11-21 |
| 07 | [`07-testing-overrides-and-isolation.md`](07-testing-overrides-and-isolation.md) | `OverridePlan` / `ProviderOverride`, the five override kinds, build-scoped activation, concurrency isolation and the pytest fixture pattern | 02, 03, 04, 05, 06 | Draft — 2026-11-21 |
| 08 | [`08-framework-compatibility-and-migration.md`](08-framework-compatibility-and-migration.md) | Symbol-by-symbol `lauren` → `lauren_di` mapping, replacements for the HTTP-bound seams, behavioural-parity test strategy, deprecation/alias policy | 01–07 | Draft — 2026-11-21 |
| 09 | [`09-integration-contract-and-host-adapters.md`](09-integration-contract-and-host-adapters.md) | The framework-agnostic adapter contract: scope ownership and close point, host-value passing, the sync-loop bridge, startup/shutdown ordering, concurrency limits, error mapping, and the conformance suite every host adapter must pass | 01–08 | Draft — 2026-11-22 |
| 10 | [`10-asgi-web-frameworks.md`](10-asgi-web-frameworks.md) | Hosting the container in Starlette 1.10.0 and FastAPI 0.165.0: lifespan start/stop, pure ASGI vs `BaseHTTPMiddleware`, `request.state` storage, streaming and background tasks as the scope close point, websockets, `Depends` coexistence, `httpx.ASGITransport` testing | 01–09 | Draft — 2026-11-24 |
| 11 | [`11-async-web-frameworks.md`](11-async-web-frameworks.md) | Hosting the container in Litestar 2.25.0, Quart 0.23.1, Sanic 25.12.1 and aiohttp 3.14.5: each framework's startup/shutdown hook, middleware signature, per-request storage, sync-vs-async handler model, streaming and close point, comparison matrix | 01–09, 10 | Draft — 2026-11-24 |
| 12 | [`12-wsgi-web-frameworks.md`](12-wsgi-web-frameworks.md) | Hosting the container in Flask 3.1.3, Falcon 4.4.0, Pyramid 2.1 and Bottle 0.13.4: thread-per-request model, the dedicated-loop bridge (D-59), `startup()` before serving (D-60), storage in `g`/`request`/`environ`, `teardown_request`/`on_close` vs `scope.close()` | 01–09 | Draft — 2026-11-24 |
| 13 | [`13-django-integration.md`](13-django-integration.md) | Django 6.1.3: `AppConfig.ready()` vs the event loop, WSGI and ASGI deployment, sync/async views and the `sync_to_async` thread hop that makes an ambient scope impossible (D-58), middleware placement, `request_started`/`request_finished`, ORM connection affinity, test client and `override_settings` | 01–09 | Draft — 2026-11-24 |
| 14 | [`14-cli-frameworks.md`](14-cli-frameworks.md) | Hosting the container in argparse, click 8.5.1, Typer 0.29.2, cyclopts 5.5.2 and fire 0.7.1: one container per invocation, `startup()`/`shutdown()` around the command, exit codes derived from `DestructError`, `ctx.obj` storage, the `Annotated`-vs-`Inject` collision, `CliRunner` testing, overrides, signal handling | 01–09 | Draft — 2026-11-24 |

A final consistency pass (goal #10) re-verifies this table against the files on
disk and records the result in §6.

### 2.1 Why this split

Each document owns one **coherent decision surface**. The boundaries were chosen
where the reference implementation itself separates concerns, so parity can be
checked file-by-file:

| PRD | Reference source(s) |
|-----|---------------------|
| 01 | `lauren/__init__.py`, `lauren/decorators.py`, `lauren/types.py`; root `pyproject.toml` for packaging conventions (siblings: `lauren-middlewares/pyproject.toml`) |
| 02 | `lauren/_di/__init__.py` — `register`, `_inspect_class_deps`, `_inspect_callable_deps`, `_collect_field_deps`, `_construct_class` |
| 03 | `lauren/_di/__init__.py` — `_instantiate`, `_check_scope`, `_find_lifecycle_hooks`; `lauren/_di/_scope.py` |
| 04 | `lauren/_di/custom.py`; `lauren/_di/__init__.py` — `_resolve_multi_list`, `_bind_token`, `register_alias` |
| 05 | `lauren/exceptions.py`; `lauren/_di/__init__.py` — `compile`, `resolve_one`, `_get_injectable_meta` |
| 06 | `lauren/_di/__init__.py` — `set_visible`, `_is_visible`, `_structural_protocol_providers`; `lauren/_modules/` (consumer only) |
| 07 | `lauren/_di/_overrides.py`; `lauren/testing/` (consumer only) |
| 08 | all of the above plus `lauren/_asgi/`, `lauren/_lifecycle/`, `lauren/extractors.py` (couplings to remove) |

Deliberately absent from this set: a PRD for **implementing** routing, ASGI, OpenAPI,
extractors, typed configuration, scheduling, stores, logging, CLI or the module-graph
compiler. Those are `lauren` features; `lauren-di` must remain usable without any of them.

PRDs 09–14 are a different kind of document: they specify how to **host** this library in
somebody else's framework (Starlette/FastAPI, Litestar, Quart, Sanic, aiohttp, Flask,
Falcon, Pyramid, Django, argparse/click/Typer/cyclopts). They are measured against those
frameworks' installed versions, not against `../lauren-framework`, so §2.1's reference
table does not apply to them; each records its own pinned versions in its §6.5. The
boundary is unchanged: no adapter adds a runtime dependency to `lauren-di`, and no
framework noun enters the library's API (D-57).

## 3. Product boundary (the one-paragraph test)

`lauren-di` answers exactly one question: **given a declared token, how do I
obtain the object bound to it, for the lifetime I asked for, with its own
dependencies supplied?** A feature belongs in this library if and only if it is
needed to answer that question. A feature that only makes sense while serving a
request, reading configuration, or composing an application module belongs to a
different library.

Consequences, stated once here and enforced per-PRD:

- The container accepts *any* hashable token. It never inspects a `Request`,
  a route, a header, a query string or a media type.
- The container owns *scope records*, not transports. `container.scope()` creates a
  `ResolutionScope` that owns its cache (PRD 03); an embedder may instead pass its
  own `request_cache` in. Nothing derives a scope from an HTTP request.
- Lifecycle hooks run because the container says so on `startup()` /
  scope exit — there is no separate scheduler to delegate to.
- Values supplied by an embedding runtime arrive through a generic
  type-keyed `values` mapping, not through a hard-coded framework type.

## 4. Decision register

The single place where cross-cutting decisions are recorded. A PRD that needs a
decision cites its ID; it does not restate the rationale. Status values are
`Open`, `Proposed`, `Accepted`, `Rejected`, `Superseded`.

| ID | Decision | Status | Owner PRD | Notes |
|----|----------|--------|-----------|-------|
| D-1 | Distribution `lauren-di`, import package `lauren_di` | Proposed | 01 | Follows the sibling packages (`lauren-middlewares` → `lauren_middlewares`). |
| D-2 | Behavioural parity with `lauren/_di` is the contract; this is an extraction, not a redesign | Proposed | 01 | Any intentional divergence is listed in 08 with a rationale. |
| D-3 | Zero runtime dependencies; stdlib only; Python ≥ 3.10 | Proposed | 01 | Forward-reference resolution must therefore be in-tree, not borrowed from a typing helper package. |
| D-4 | Async-first resolution (`await container.resolve(...)`) | Proposed | 02 | Sync providers still work; async factories, hooks and teardown are first-class. A sync API is not part of v1. |
| D-5 | Module encapsulation: v1 or v2 | Accepted | 06 | Resolved by D-37: the visibility primitives ship **active in v1**; the declarative module graph is out of scope in v1 and v2. The "inert in v1" option is rejected, with its contract preserved in PRD 06 §5.9. |
| D-6 | `Depends[T]`: extractor-style marker or plain alias | Accepted | 04 | Resolved by D-25 (a spelling-only alias) once D-54 fixed the seam D-6 was waiting on: the predicate hook is where marker awareness now lives, and `Depends` needs none of it. |
| D-7 | Singleton lifecycle hooks are fired by the container, not a scheduler | Proposed | 03 | The container exposes `startup()` / `shutdown()`; embedding frameworks call them. |
| D-8 | Framework value seeding becomes a generic type-keyed `values` mapping | Accepted | 03, 08 | Replaces the hard-coded `Request` special case. Resolved by D-52: the declaration is per-container (`value_tokens=`), not a process-global registry. |
| D-9 | Field collection's `ExtractionMarker` awareness becomes a pluggable predicate | Accepted | 02, 08 | Resolved by D-54. The predicate is optional and stdlib-only, so `lauren` can keep using `lauren-di` underneath without the library importing `lauren.extractors`. |
| D-10 | Migration aliasing/deprecation policy for `lauren` users | Accepted | 08 | Resolved by D-50: rename-only in the library; a dated `lauren.compat` shim in the framework for one minor release; no reverse dependency. |
| D-11 | Marker attribute names (`__lauren_injectable__` → `__lauren_di_injectable__`) | Accepted | 08 | Resolved by D-51, extended to both hook markers. The raw constant is not exported, so the rename is the only migration surface. |
| D-12 | `OverridePlan` carries provider overrides only; module substitution is dropped | Proposed | 07 | There is no module graph to substitute; `lauren` keeps module swapping in its own test kit. |
| D-13 | `resolve(..., framework_values=...)` is renamed to `resolve(..., values=...)` | Accepted | 02, 08 | Resolved by D-52: the keyword is renamed, `request_cache` keeps its name, and the old keyword is translated only by the framework's shim. |
| D-14 | Resolution compiles the graph lazily on first use | Proposed | 02, 05 | `compile()` stays public for eager validation; registration alone never validates. |
| D-15 | Any `register*` call after `compile()` clears the compiled flag, so the next validation re-checks the graph | Proposed | 02, 05 | The reference keeps `_compiled=True`, so a provider added after compile is never validated. |
| D-16 | `register()` rejects a target that is neither a class nor a callable with `InvalidProviderError` | Proposed | 02, 05 | The reference silently installs an instance as a function provider whose token is the instance; `resolve(TheClass)` then fails. |
| D-17 | A generator/async-generator provider may not use `Scope.TRANSIENT`; registration raises `LifecycleConfigError` | Proposed | 02, 03, 05 | Teardown for a transient instance could never run. The reference raises its `StartupError` base for the same case. |
| D-18 | Error leaves are exported symbols; `DIError` is their common base and the catch-all | Proposed | 05 | Users write `except MissingProviderError:`; `__all__` therefore includes the leaves PRD 05 enumerates, on top of PRD 01 §5.3.1–§5.3.5. |
| D-19 | A positional-only parameter that would be collected as a dependency is rejected at registration with `UnresolvableParameterError` | Proposed | 02, 05 | Dependencies are injected by keyword, so such a parameter can never be satisfied. The reference collects it and then raises `TypeError` at construction. Positional-only parameters that are *not* collected are unaffected. |
| D-20 | Teardown surfaces its failures as values: `shutdown()` and `ResolutionScope.close()` return `tuple[DestructError, ...]`, and `ResolutionScope.errors` exposes the same | Proposed | 03, 05 | A rule that is only logged cannot be tested or handled. Teardown never raises for hook failures; the original body exception still propagates unchanged. |
| D-21 | No per-hook teardown timeout and no implicit thread offload: sync hooks run inline on the event loop | Proposed | 03 | `DestructTimeoutError` is not carried (PRD 01 §7). Moving user code across threads silently breaks loop-bound hooks. Callers bound the whole call with `asyncio.wait_for(container.shutdown(), n)`. |
| D-22 | `startup()` and `shutdown()` are idempotent; `shutdown()` clears the singleton cache and the container is restartable | Proposed | 03 | The reference re-fires every `@post_construct` on a second `startup()` and keeps the cache after `shutdown()`, so `resolve()` hands back an already-destroyed instance. |
| D-23 | `LifecycleViolationError` is an exported leaf for a `ResolutionScope` used outside its lifetime | Proposed | 03, 05 | PRD 01 §7 listed it as not carried; a single-use scope object needs exactly this error, and `LifecycleConfigError` describes declaration-time misconfiguration instead. |
| D-24 | Teardown is one reverse-creation-order pass for both caches; a singleton generator's `aclose()` runs at its own position | Proposed | 03 | The reference tears singleton generators down in a second full pass after every `@pre_destruct`, so "reverse creation order" means two different orders. |
| D-25 | `Depends[T] is T`: a spelling-only alias that produces no marker, no wrapper and no runtime object; no `ExtractionMarker` base class is carried | Proposed | 04 | The reference needs `Annotated[T, Depends]` only because `lauren/extractors.py` owns a marker-stripping layer. `lauren-di` has no extractor layer, so the marker would be dead weight and a false dependency direction. |
| D-26 | A scalar resolve of a token with more than one visible binding raises `ProtocolAmbiguityError`; a collection is only ever spelled `list[T]` | Proposed | 04 | The reference returns a `list` from a scalar token once two `multi=True` rows exist. A return type that depends on registration count cannot be annotated or tested, and `list[T]` already spells "collection". |
| D-27 | An alias has no scope of its own: consumer edges through it are validated against the terminal target, and `get_provider(alias).scope` reports that target's scope | Proposed | 03, 04 | The reference stamps every alias row `SINGLETON` and never checks edges through it, which smuggles a request-scoped dependency into a singleton. |
| D-28 | A self-alias is rejected at registration with `DecoratorUsageError`, not at `compile()` as a cycle | Proposed | 04 | The reference accepts it and reports `CircularDependencyError {"cycle": ["A", "A"]}`. A one-token self-alias is a typo; failing where it is written is cheaper than at validation. |
| D-29 | Duplicate and ambiguity errors share one `detail` shape — `token`, optional `element`, `count`, optional `hint` — at every raise site | Proposed | 04, 05 | The reference has three shapes across three raise sites, one of them empty (`{}`). The error must be actionable programmatically; parsing messages is not a contract. |
| D-30 | A bare `Token` in a class body is rejected at registration with `UnresolvableParameterError` naming `Annotated[T, Inject(token)]`, instead of being silently dropped | Proposed | 04, 05 | The reference skips the field and leaves the instance without the attribute, so the failure surfaces later as `AttributeError` at first use. |
| D-31 | Validation is a fixed six-phase pipeline — token index, alias targets, cycles, missing providers, scope narrowing, finalise — and it is fail-fast: one error, no aggregation | Proposed | 05 | The reference interleaves a DFS with a trailing sweep, so which error a broken graph reports depends on registration order. A catalogue cannot describe order-dependent behaviour. |
| D-32 | `compile()` is synchronous and side-effect-free: it never constructs a provider, never awaits and never calls user code, so there is no `compile_async()` | Proposed | 05 | Validation reads the `Provider` records only. An async signature would imply it can construct, which would make "compile" mean "build". |
| D-33 | One private renderer produces every token spelling: `Plugin`, `"SETTINGS"`, `Token('DB', unique=False)`, `list[Plugin]` | Proposed | 05 | The reference renders `list` for `list[Plugin]`, drops quotes and prints `Token(DB)`, so two different tokens can print identically in a message or a `detail` value. |
| D-34 | A validation error that discovered a token through an edge names the consumer: `dependent` and `param` are added to `token` | Proposed | 05 | The reference reports `{"token"}` or `{"count"}` only; "No provider for Mailer" costs a search, "required by Report.mailer" is the fix. |
| D-35 | `DestructError` is returned by teardown and never raised, while still subclassing `DIError` so `except DIError` catches it | Proposed | 03, 05 | A teardown failure must not abort the remaining teardown (D-20), but callers still want one catchable root. Supersedes PRD 01 §7's "raised, never returned" phrasing. |
| D-36 | Declaration errors stay at declaration time: `MetadataInheritanceError`, `UnresolvableParameterError`, `InvalidProviderError`, `LifecycleConfigError` and `DecoratorUsageError` are raised by the decorator or `register*`, never by `compile()` | Proposed | 02, 05 | A provider that cannot be introspected must not enter the registry, so the pipeline never needs a metadata phase; the reference behaves the same way, and the rule is recorded so a rewrite cannot quietly defer it. |
| D-37 | Module visibility primitives ship **active in v1**; the declarative module graph (`@module`, imports, exports, `ModuleGraph`, substitution) ships in neither v1 nor v2 | Accepted | 06 | Resolves D-5. An inert `owning_module` would silently widen resolution for migrators; the primitives cost ~150 lines, are opt-in, and default to the flat registry. The graph is a framework concern (PRD 01 §3). |
| D-38 | With encapsulation on, `owning_module=None` on a **resolution** means the global view: only rows registered with `owning_module=None` | Proposed | 06 | The reference falls back to the flat registry, which makes any unscoped call a universal bypass (probe E-201). A named, narrow domain keeps the ambiguity rule decidable. |
| D-39 | Encapsulation is container-wide and fail-closed: the first declaration switches it on for every module-scoped call, and an undeclared module raises instead of seeing everything | Proposed | 06 | The reference fails open per module (probe E-203) and lets `set_visible(M, ∅)` hide `M`'s own providers (probe E-209). Both are silent; a missing declaration is a wiring bug and must be loud. |
| D-40 | Compile-time ambiguity is evaluated per visibility domain, not over the flat token table | Proposed | 06 | The reference rejects two modules that each bind one implementation of a Protocol (probe E-207) — the canonical encapsulation case. With one domain the rule degenerates to PRD 05's sweep. |
| D-41 | Grants are row-addressable: a bare token covers every row bound to it, `(token, owner)` addresses one row, and a module's own rows are always visible to it | Proposed | 06 | The reference compares `provider.cls` to the grant set, so a `provides=[Proto]` row is missed by a grant on `Proto` (probe E-215) and multi siblings cannot be separated (probe E-217). |
| D-42 | A structural-Protocol ambiguity carries `token`, `count` and `modules` | Proposed | 06 | The reference raises the class with an empty `detail` on that path (probe E-208c), so the message is the only handle. PRD 05 §5.7 requires every raise site to carry keys. |
| D-43 | `set_visible` / `grant_visible` invalidate `compile()`, like `register*` | Proposed | 06 | The reference mutates live resolution without clearing `_compiled` (probe E-225), so a widening after validation is never re-checked. D-15 already establishes the pattern for registration. |
| D-44 | Overrides ship in v1 as a container feature (`DIContainer(overrides=…)`); module substitution and the HTTP override roles are out of scope | Accepted | 07 | A DI-only container has no module graph and no guards, so `OverridePlan.modules` and `override_guard`-style roles have nothing to act on (D-37 keeps the graph out). The provider payload is the whole DI concern. |
| D-45 | Override payloads are validated when the plan is built; only target existence is checked at `compile()` | Proposed | 07 | The reference validates the payload inside `register`, so a bad plan depends on registration order and leaves a half-registered row that compiles cleanly (probes E-316, E-316b). |
| D-46 | Every override kind inherits the target's scope; `scope=` may narrow, never widen | Proposed | 07 | The reference forces `SINGLETON` for `value`/`existing` (probe E-304), so a request-scoped double is shared across requests (E-302c) and a masked scope violation passes `compile()` (E-303b). |
| D-47 | `use_values` on a single-row token is an error, not a silent promotion to `multi` | Proposed | 07 | The reference flips the row's `multi` flag (probe E-305), changing `resolve(T)` from `T` to `list[T]` with no compile phase noticing. |
| D-48 | Scope narrowing is validated against the override row's effective scope, so an override can never mask a real violation | Proposed | 07 | Follows from D-46: the violation in probe E-303 disappears only because the row's scope changed. |
| D-49 | A plan is frozen and read once per container; activating a different plan later cannot change an existing container | Proposed | 07 | The reference documents `OverridePlan` as immutable but stores mutable dicts (E-319), and a late registration can observe a mutation. Freezing removes the aliasing question. |
| D-50 | Migration is rename-only in the library; a dated `lauren.compat` shim lives in the framework for one minor release, and the dependency arrow never reverses | Accepted | 08 | Resolves D-10. A shim inside `lauren-di` would make the DI library import the framework (MC-23 forbids it), and rename-only alone gives a 200-class user no mechanical path. The shim re-exports rather than re-implements and carries a `Removed in` version (MC-27). |
| D-51 | Marker attributes are renamed `__lauren_injectable__` → `__lauren_di_injectable__`, `__lauren_post_construct__` → `__lauren_di_post_construct__`, `__lauren_pre_destruct__` → `__lauren_di_pre_destruct__` | Accepted | 08 | Resolves D-11 and extends it to both hook markers (`E-401`, `E-401b`, `E-401c`). The raw constant is not exported (PRD 01 §5.3.6), so `injectable_meta()` is the only reader and the rename touches no public API. |
| D-52 | `framework_values` becomes `values`, and the process-global `register_framework_value` registry becomes a per-container `DIContainer(value_tokens=…)` | Accepted | 02, 08 | Resolves D-8 and D-13. The reference's registry is import-time global with no unregister (`E-414`…`E-416b`), so one test's declaration changes another container's `compile()` (`E-415`); a declared token also fails mid-construction rather than at scope entry (`E-412`). |
| D-53 | Lifecycle is container-owned: `startup()`/`shutdown()` replace `LifecycleScheduler`; the scheduler's write-back hooks (`set_singleton`, `mark_singleton_initialized`) and `singletons` are internalised | Accepted | 03, 08 | Extends D-7 with the reference's measured shape. Hooks fire only under an external scheduler (`E-420`, `E-423`). `adopt_lifecycle()` stays public as the embedder seam for foreign objects, and `instance_index()` as a read-only diagnostic (D-56). |
| D-54 | `ExtractionMarker` awareness is replaced by an optional, stdlib-only `field_predicate(name, annotation)`; the eleven HTTP extractor markers are explicitly not supported | Accepted | 02, 08 | Resolves D-9. The reference imports `lauren.extractors` inside the DI core (`_di/__init__.py:2313`), so `Annotated[str, Path]` on a class field becomes a dependency on `str` and fails with `MissingProviderError` (`E-404`, `E-405`) — a message about `str`, not about the misplaced marker. |
| D-55 | Behavioural parity is asserted by an adapter-parameterised harness that pins the reference artifact and reports SKIP (never PASS) for an absent backend | Accepted | 08 | Prototyped green on the reference (`E-430`…`E-433`). The pin is load-bearing: this environment's default `lauren` is an installed build 420 lines shorter than `../lauren-framework`, so an unpinned run measures the wrong code (§1.5). |
| D-56 | Public lifecycle surface after internalising the scheduler: `adopt_lifecycle()` and `instance_index()` stay public; `set_singleton()`, `mark_singleton_initialized()` and `singletons()` do not | Accepted | 01, 03, 05, 08 | Consistency pass (goal #10). PRD 03 §5.9 needs `adopt_lifecycle` for objects the container did not construct and `instance_index` for teardown auditability; the other three existed only so the reference's `LifecycleScheduler` could write back. Tests observe the cache through identity and hook firing, never by peeking. |
| D-57 | No framework-specific module ships in `lauren-di`: a host integration is a documented adapter, and a packaged adapter would be a separate distribution depending on both projects | Proposed | 09 | Keeps `dependencies = []` (D-3) and the no-HTTP-vocabulary rule (PRD 01 §9). An adapter inside the library would make every host framework's release cadence a release constraint here, and `import flask` in the package is already a test failure (`tests/packaging/test_no_framework_imports.py`). |
| D-58 | The integration seam is the explicit `ResolutionScope`; v1 adds no ambient `current_scope()` ContextVar | Proposed | 09 | A ContextVar does not survive a WSGI thread hop or Django's `sync_to_async`, so an ambient scope would be silently **absent** exactly where a host needs it. Hosts store the scope in their own request state (PRD 09 §5.3), which is one line per framework and cannot go missing without a `KeyError`. |
| D-59 | A synchronous host owns its event-loop bridge; `lauren-di` ships no bridge | Proposed | 09 | Two measured patterns (PRD 09 §5.5): one long-lived loop on a dedicated thread (≈0.20 ms per call, 0.18–0.22 across five runs; preserves loop affinity and removes the cross-thread construction race) or `asyncio.run()` per unit of work (0.24–0.33 ms across five runs, and a loop-bound singleton then fails with `RuntimeError: Event loop is closed`). Which is correct depends on the host's loop model, so the library cannot choose. |
| D-60 | Singleton construction is not serialised; a multi-threaded host must call `startup()` before serving | Proposed | 09 | Measured: eight threads racing the first resolve of one singleton construct eight instances and keep one — seven leak. After `startup()` the same race yields one. v1 adds no lock: a blocking lock cannot be held across an `await`, and a per-loop `asyncio.Lock` does not serialise threads. The supported contract is eager construction on one thread before the server binds. |
| D-61 | The host adapter owns the scope lifetime: open after the host has bound the request/job/command, close in a `finally` after the response body (or command) has finished | Proposed | 09 | Streaming responses and background tasks make "after the handler returns" wrong, so PRD 09 §5.2 fixes the close point per host shape. Teardown errors are returned and reported through the host's log or error channel, never raised into a response. |
| D-62 | Compatibility is proved by a skip-guarded conformance suite with pinned framework versions; an absent framework is SKIP, never PASS | Proposed | 09 | Mirrors D-55. PRD 09 §5.10 defines the invariants; each compatibility page pins the versions it measured in its §6.5, so a reader can tell which artifact a claim describes. |
| D-63 | The PRD-level "no web-framework import" rule applies to the specification pages (01–08); compatibility pages (09–14) may show a framework import only inside a `fragment`/`skip` fence | Proposed | 09 | Resolves the conflict between PRD 01 §9 and a page whose subject **is** the framework. `run` fences execute corpus-wide in an environment with no framework installed, so a framework sample marked `run` would fail the corpus test. The library-level guarantee is unchanged and still enforced by `dependencies = []` and `tests/packaging/test_no_framework_imports.py`. |

## 5. Status vocabulary and workflow

- **Planned** — promised by the index, not written yet. The index names the goal
  that will write it.
- **Draft** — written, internally consistent, open for review.
- **Accepted** — reviewed; downstream PRDs may rely on it as settled.
- **Superseded** — replaced; the replacement is linked from the header.

A PRD may not move to `Accepted` while it references an `Open` decision; it may
be `Draft` with open questions, as long as every open question is in §4 rather
than buried in prose.

## 6. Verification

The final consistency pass (goal #10) checks, and this file records:

1. **Index/file parity** — the table in §2 lists exactly the files present in
   this directory.
2. **Template completeness** — every PRD contains all ten numbered template
   headings from §1.
3. **Reference integrity** — every relative link and every `D-n` citation
   resolves; every `PRD NN` citation names a document that exists, and every
   `PRD NN §x` names a heading of that document; every decision marked
   `Accepted` is decided in exactly one PRD.
4. **Symbol consistency** — the same public name, default and scope assignment
   is used across all PRDs (a generated symbol/name diff, not eyeballing).
5. **Scope discipline** — no PRD reintroduces an HTTP, routing, extractor,
   config, scheduling or module-graph concern except as an explicit
   compatibility note in 08 or in the host-compatibility pages 09–14, which may
   show a framework import only inside a `fragment`/`skip` fence (D-63).
6. **PRD code blocks** (goal #50) — every fenced `python` block in
   `prds/[0-9][0-9]-*.md` compiles, including the ones marked `fragment`/`skip`,
   and every block carries a marker: `run` means a reader is meant to type it and
   `tests/docs/test_prd_examples.py` executes it, `fragment` (or `skip`) means
   deliberately partial. An unmarked block is a failure, because an unmarked
   block is one no gate has decided about — which is how the corpus came to hold
   88 blocks that nothing compiled or ran. The gate then reads its own census back
   out of this file's `PRD code blocks` row below, so the printed census and the
   published one are the same five numbers: the row must exist exactly once, must
   parse, and must match `blocks`, `pages`, `compile`, `run`, `fragment`/`skip` and
   `unmarked`.
7. **Divergence registers** (goal #56) — each `§6.4 Intentional divergences from
   the reference` table is numbered `1..n` with no holes, and its prose agrees
   with it: a sentence naming `§6.4` with a typed count must state the register's
   real size (or the size of the row range it names), and a closing sentence may
   only claim "same silent cases" when no row records a silent-case divergence.
   The cross-register link is checked both ways as well: every `P-n` named in a
   `§6.4` row must exist in PRD 08 §5.9, every `PRD NN §6.4 row N` citation must
   resolve to a row that names that `P-n` back, and every §5.9 row marked
   `**divergence:**` must be named by the register sentence.

Result of the last run: **passed** (2026-11-24, the 09–14 integration pass). Rerun with
`python3 prds/check_consistency.py` — exit 0 means clean; the script *is* the check, not a
description of it. Its teeth were tested: six seeded regressions (a `shutdown()` returning `None`,
a dangling link, a renamed section, a non-existent cross-reference, a deleted decision row, and an
unguarded `from lauren import`) each make it exit 1.

| Check | Result |
|-------|--------|
| Index/file parity | 14 files on disk, 14 rows in §2, no duplicate rows, no dangling row |
| Template completeness | 14/14 PRDs × 10 sections, same titles and order |
| Reference integrity | 0 dangling relative links; 197 `PRD 0N §x` cross-references and 538 `PRD NN` mentions resolve; D-1…D-63 defined exactly once, contiguous, no undefined citation, and no decision left in the `Open` state (47 remain `Proposed` by design — every one of them is a row in §4) |
| Symbol consistency | curated table green: `shutdown()` return, `Depends[T] is T`, the public surface, `resolve` keywords, five override kinds and their scope-inheritance rows, `Scope` = 0/1/2, the 18-field `Provider`, and no `container.close()` |
| Scope discipline | script-checked: pages 01–08 import no framework, pages 09–14 show one only in a `fragment`/`skip` fence (D-63), and in PRDs 01–07 a code sample may `import lauren` only inside PRD 02's `LAUREN_DI_PARITY_TARGET`-guarded shim (0 unguarded samples); `HTTP`/`ASGI` nouns otherwise appear only in non-goals, prior art and the compatibility pages |
| PRD code blocks | 118 python blocks in 14 pages — 118 compile, 31 marked `run` (executed by `tests/docs/test_prd_examples.py`), 87 `fragment`/`skip`, 0 unmarked |
| Divergence registers | 3 `§6.4` registers — PRD 02's 9 rows, PRD 03's 11, PRD 04's 10 — every typed count and parity claim agrees with its row set, and PRD 08 §5.9's 14 invariants cross-reference them both ways |

### 6.1 Findings fixed by this pass

Five contradictions and two stale cross-references were found and reconciled:

1. **`DIContainer.shutdown()`** — PRD 01 declared `-> None`, while PRD 03 and D-20 declare
   `-> tuple[DestructError, ...]` and PRD 03 already claimed PRD 01 had been updated. PRD 01 corrected.
2. **`Depends`** — PRD 01 and PRD 02 still described `Depends[T]` as `Annotated[T, Depends]`, but
   D-25 settles `Depends[T] is T`. PRD 01 §5.3.1 and PRD 02's gate table, R-23 and §5.9 prose corrected.
3. **`resolve` typing contract** — PRD 01 §5.6's overloads omitted `request_cache` and
   `owning_module`, contradicting its own §5.3.3 row. The overloads now carry the full keyword set.
4. **Public lifecycle surface** — PRD 01 and PRD 03 documented `singletons()`, `set_singleton()` and
   `mark_singleton_initialized()` as public while PRD 08 removed them; PRD 08 also grouped
   `singletons` with the removed mutators while its own MC-17/T08-17 did not mention it. Resolved by
   **D-56**: `adopt_lifecycle()` and `instance_index()` stay public; the other three do not. PRD 01,
   03, 05, 08 and D-53 updated, and PRD 03's tests now observe the cache through object identity and
   hook firing instead of peeking.
5. **Stale present-tense cross-references** — PRD 03 §1.6 ("PRD 01 currently fixes `shutdown() -> None`",
   "sits in PRD 01's *not carried* list") and PRD 05 §1.4 (the same two claims) described drift that
   PRD 01 had already fixed. Both reworded to past tense with the resolution named.

Item 4 was a genuine design fork rather than a typo, so it was put to the owner and decided as D-56
instead of being guessed.

### 6.2 The host-compatibility pass (09–14)

Pages 09–14 were written and verified against installed frameworks rather than against
`../lauren-framework`, so this pass changed the register and the gate as well as the counts above:

1. **D-57…D-63 added, all `Proposed`.** D-57 keeps adapters outside the package; D-58 refuses an
   ambient `current_scope()` ContextVar because it does not survive a WSGI thread hop or Django's
   `sync_to_async`; D-59 leaves the sync-loop bridge to the host; D-60 requires `startup()` before a
   threaded server binds; D-61 fixes the close point per host shape; D-62 makes an absent framework
   a SKIP; D-63 resolves the conflict between PRD 01 §9's no-framework-import rule and pages whose
   subject *is* the framework.
2. **The scope check learned the split.** `prds/check_consistency.py` now permits a framework import
   only in a `fragment`/`skip` fence on pages 09–14. A `run` fence executes corpus-wide in an
   environment with no framework installed, so a framework sample marked `run` would fail the
   corpus test — which is why D-63 names the two allowed markers rather than "compatibility pages
   are exempt".
3. **Verification was adversarial, and it paid.** Re-running each page's probes end to end found
   three probes that had been producing false evidence — a Typer one-command app invoked with its
   own subcommand name (Typer's single-command collapse ate it, manufacturing `exit=2` "failures"),
   a click group that routed subcommands through the wrong root, and an override probe that crashed
   before recording its result — plus one claim (PRD 14 H-24) whose quoted cost range excluded a
   measurement in its own transcript. All four were fixed at the probe and the page was rewritten
   from the fresh transcript; no page now cites a number that a re-run does not reproduce.

## 7. Adding a PRD

1. Take the next free number; numbers are never reused.
2. Copy the ten-section template from §1.
3. Add a row to §2 with its concern, dependencies and status.
4. If the PRD decides something cross-cutting, add or update a `D-n` row in §4
   instead of only describing it locally.
5. Update §6 with the verification result once the consistency pass is rerun.
