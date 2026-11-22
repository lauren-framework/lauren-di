# PRD 01 — Product Definition & Public API

**Status:** Draft
**Date:** 2026-11-21
**Author:** DI extraction study (2026-11-21)
**Scope:** `prds/01-product-definition-and-public-api.md` — product boundary, package identity,
supported interpreters, dependency policy, the complete exported-symbol table and per-symbol
usage examples, the quickstart, and the non-goals. It does not specify container internals;
those belong to PRDs 02–07.
**Type:** Product definition + API specification
**Priority:** P0 (every later PRD depends on the names and defaults fixed here)
**Target release:** `0.1.0` (first publishable cut), API freeze at `1.0.0`
**Depends on / related:** `prds/README.md` §3 (product boundary) and §4 (decision register);
consumed by PRDs 02–08; release scaffolding is goal #11.
**Inspired by:** NestJS providers and `@Injectable()`, FastAPI `Depends`, `dependency-injector`,
`injector`, `punq`, `dishka`, `wireup`, Spring's `@Component`/`@Scope`, and .NET's
`IServiceCollection` lifetimes.

---

## 1. Problem Statement

Dependency injection is the most reusable subsystem in `lauren`, and the only one that is
**not** inherently about HTTP. It currently lives inside an ASGI framework, which forces three
costs on anyone who wants it.

### 1.1 The container cannot be adopted without the framework

`DIContainer`, `@injectable`, `Scope`, `Token` and the four custom-provider recipes are reachable
only through `lauren`'s package root, whose import graph pulls in routing, ASGI machinery,
configuration, extractors and the module compiler. A CLI tool, a worker process, a data pipeline
or a *different* web framework cannot take the container without also taking an application
framework it will never start.

### 1.2 HTTP vocabulary leaks into the injection contract

Three seams in the DI core are shaped by the framework rather than by injection:

| Seam | Current shape | Why it is wrong for a library |
|------|---------------|-------------------------------|
| Runtime-supplied values | `resolve(..., framework_values=...)` with `Request` special-cased in `compile()` | The container knows about HTTP requests it will never see |
| Field collection | `_is_extractor_marker()` imports `lauren.extractors` | A DI collector has no business knowing what a `Query` or `Path` marker is |
| Singleton startup hooks | `LifecycleScheduler` in `lauren/_lifecycle/` fires `@post_construct` | A container that owns a lifetime should own the transition into and out of it |

Each is replaceable by a generic concept (a type-keyed `values` mapping, a field predicate,
container-owned `startup()` / `shutdown()`). PRD 08 records the mappings.

### 1.3 Packaging is ambiguous

There is no distribution named `lauren-di`, no `lauren_di` import path, no PEP 561 marker, and no
statement of which Python versions or runtime dependencies the subsystem promises. A user cannot
install "just the DI part", and an embedding framework cannot declare a version constraint on it.

### 1.4 The boundary is undocumented, so it keeps expanding

Because DI lives next to modules, routing, config and scheduling, every one of those features has
grown tendrils into `_di`. Without a written boundary and an explicit non-goals list, the
extraction will drift back into a framework. This PRD fixes that boundary in one place and lets
every later PRD cite it.

## 2. Goals

| # | Goal | Acceptance signal |
|---|------|-------------------|
| G1 | Ship `lauren-di` as an installable distribution exposing `lauren_di` | `pip install lauren-di` then `import lauren_di` works with no other package on the path |
| G2 | Inject without any framework or transport | The full §5.2 quickstart runs in a bare `python` process with zero `lauren` imports |
| G3 | Keep the public vocabulary users already know | `injectable`, `Scope`, `Token`, `Inject`, `Depends`, `use_*`, `DIContainer`, `Provider`, `InjectableMeta` all exist with the signatures in §5.3 |
| G4 | Promise a small, auditable dependency surface | `dependencies = []` in the distribution metadata; `pip show lauren-di` lists no `Requires` |
| G5 | Promise interpreters the code actually supports | `requires-python = ">=3.10"`; CI runs 3.10–3.14; no 3.11-only stdlib API is used |
| G6 | Be typed for downstream users | `py.typed` in the wheel; `mypy --strict` and `pyright` pass on the examples in §5.4 |
| G7 | Make the boundary enforceable | The non-goals in §3 name every adjacent framework subsystem, and PRD 08 turns each into an explicit replacement or a recorded "not supported" |
| G8 | Give every public name one canonical example | Every symbol in §5.3 has an entry in §5.4 — one `####` subsection per symbol or tight pair, plus one grouped block shared by the thirteen `DIError` leaves — and every §5.4 block is compiled, with the `run` ones executed, by the PRD 01 §10 row 3 test (`tests/docs/test_prd_examples.py`) |

## 3. Non-Goals

The list is normative: a feature in this list is not "not yet done", it is **out of scope**, and a
PR that adds it to `lauren-di` should be rejected in favour of a different library.

- **HTTP request objects.** No `Request`, `Response`, `Headers`, `QueryParams`, cookies, bodies,
  media types, status codes or ASGI scopes. Runtime-supplied values arrive as an opaque
  type-keyed `values` mapping (D-8).
- **Routing.** No `@controller`, route registration, path templates, converters, routers,
  mounts, URL generation or versioning.
- **Extractors.** No `Path`, `Query`, `Header`, `Body`, `Form`, `Bytes`, `UploadFile` or
  `ExtractionMarker`. `Depends[T]` survives only as a DI declaration marker (§5.4).
- **Scheduling.** No `@scheduled`, cron parsing, hosted services, worker supervision, clocks or
  a `LifecycleScheduler`. Startup and shutdown are container methods (D-7).
- **The module-graph compiler.** No `@module`, no `imports`/`exports`/`controllers`, no
  transitive visibility graph, no module-level override substitution. *Declared* visibility
  sets ship as a container primitive (D-37, PRD 06); a graph compiler never does.
- **Configuration loading.** No env/file/secret sources, coercion, validation or typed settings.
  Config values can be registered as ordinary providers, nothing more.
- **CLI.** No console script, command parser, project introspection or code generation.
- **OpenAPI / serialisation / validation.** No schema generation, pydantic or msgspec
  integration, body codecs, pagination or response contracts.
- **Stores, sessions, events, background jobs, health probes, logging configuration, static
  files, WebSockets, SSE.** All are framework features, some of which *consume* DI.
- **A lifecycle scheduler or application object.** The library exposes a container and a scope
  manager; an application is something an embedding framework builds.
- **Sync resolution.** `await` is the only resolution API in v1 (D-4). A blocking helper is a
  possible future addition, not a v1 promise.
- **Automatic constructor injection without a declaration.** The container is explicit: a class
  must be marked `@injectable()` (or registered through `use_class`). It never scans imports.

## 4. Prior Art

| System | What it gets right | What we deliberately do differently |
|--------|--------------------|------------------------------------|
| NestJS | Module-scoped providers, `useValue`/`useClass`/`useFactory`/`useExisting`, `multi` collections | No modules in core (optional policy in PRD 06); decorators must be called (`@injectable()`), no implicit scanning |
| FastAPI | `Depends` reads well at the parameter site; generator dependencies are excellent for setup/teardown | `Depends` here is a DI declaration, not an HTTP extractor; no request/response types |
| `dependency-injector` | Explicit providers, configuration providers, wiring | No container-global mutable configuration object; providers stay records, not builder DSL objects |
| `injector` | Small, pure-Python, `Module`/`Inject` vocabulary | We keep `@injectable()` declarations rather than binding modules as the primary unit |
| `punq` | Minimal service registration API | We keep scopes and lifecycle, which `punq` omits |
| `dishka` | Scope-centric design, framework-agnostic core, explicit scope entry/exit | We keep `Scope` as an ordered enum on the provider, not a per-call scope key graph |
| `wireup` | Clean typing story, explicit injection | We preserve the existing `@injectable()` + `Annotated` surface for migration parity (D-2) |
| Spring / .NET DI | Lifetime vocabulary (singleton/request/transient), constructor injection as default | We do not own an application context or a host; lifecycle entry/exit is the embedder's call |

The common lesson: **the container is a library, the application is not.** Every row above that
bundles hosting into the container is the mistake this PRD exists to avoid.

## 5. Proposed API

### 5.1 Package identity and installation

```toml
# pyproject.toml (the parts this PRD fixes)
[project]
name = "lauren-di"
requires-python = ">=3.10"
dependencies = []                 # G4: stdlib only, no extras required
license = "MIT"
classifiers = [
  "Typing :: Typed",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Programming Language :: Python :: 3.14",
]

[project.optional-dependencies]   # contributor-only, never required by users
dev = ["pytest>=8.0", "pytest-cov>=5.0", "coverage[toml]>=7.6", "mypy>=1.10",
       "ruff>=0.9", "nox>=2024.4.15", "build>=1.2"]
release = ["build>=1.2", "twine>=5.0"]
docs = ["mkdocs>=1.6", "mkdocs-material>=9.5", "pymdown-extensions>=10.7"]
```

- **Import package:** `lauren_di` (underscore), distribution `lauren-di` (hyphen), matching the
  sibling convention `lauren-middlewares` → `lauren_middlewares` (D-1).
- **PEP 561:** `lauren_di/py.typed` is shipped in the wheel and listed in package data. No
  inline `type: ignore` is required to use the public API.
- **Runtime dependencies:** none, and no module *requires* anything outside the standard library —
  the wheel installs and imports in an environment that contains nothing else (G4). The
  forward-reference resolver is in-tree (§6.4). The one third-party name a module *consults* is
  `typing_extensions`, and only when that package is already installed — a **guarded optional
  import**, declared in §6.2, never required: `dependencies = []` (G4) is the claim, and the
  clean-venv test imports the built wheel in an environment without `typing_extensions` to prove
  the resolver works when it is absent. The absolute here is about *requirements*, not about
  imports: a guarded import of an installed package is not a dependency.
- **Optional extras:** contributor-only, never required by users, and there are no runtime
  extras: exactly `dev` (pytest, coverage, mypy, ruff, nox, and the wheel builder used by
  `tests/packaging/`), `release` (`build`, `twine`) and `docs` (mkdocs and its extensions). The
  normative rule is **no runtime extras or runtime dependencies**: `dependencies = []` (G4) is
  the claim a user can verify, and because the extras exist the built wheel does carry
  `Requires-Dist` lines, but there is no *unconditional* `Requires-Dist` — every
  `Requires-Dist` is `extra ==`-gated. The key set of
  `[project.optional-dependencies]` above is what a contributor installs from; it is asserted
  against `pyproject.toml` by `tests/packaging/test_wheel.py`.
- **Python floor:** 3.10 (G5). The reference `_di` implementation uses only
  `inspect.get_annotations`, `typing.get_origin`/`get_args`, `dataclasses`, `enum`,
  `contextvars` and `asyncio` — all available on 3.10. The sibling packages declare `>=3.11`;
  `lauren-di` intentionally goes one version wider because the DI core is the least dependent
  part of the stack.
- **No framework requirement:** `lauren` is not a dependency in either direction. An embedder
  (including `lauren` itself) may depend on `lauren-di`, never the reverse.

### 5.2 Quickstart

Twenty lines, no framework, no transport:

```python run
import asyncio
from lauren_di import DIContainer, Scope, injectable

@injectable()
class Config:
    dsn = "sqlite:///app.db"

@injectable()
class Session:
    def __init__(self, cfg: Config) -> None:      # constructor injection
        self.dsn = cfg.dsn

@injectable(scope=Scope.REQUEST)
class UserRepo:
    session: Session                              # class-body field injection

async def main() -> None:
    container = DIContainer()
    for cls in (Config, Session, UserRepo): container.register(cls)
    assert (await container.resolve(Config)).dsn == "sqlite:///app.db"   # singleton
    async with container.scope() as scope:
        repo = await scope.resolve(UserRepo)      # request-scoped
        assert repo.session.dsn == "sqlite:///app.db"

asyncio.run(main())
```

What the snippet demonstrates, in order: `@injectable()` on plain classes, a default singleton,
constructor-parameter injection, class-body field injection, an explicit request scope, explicit
registration, lazy graph validation on first resolution, `await container.resolve(...)` for a
container-scoped singleton, and `await scope.resolve(...)` through a request-scope record.

### 5.3 Exported symbols

`lauren_di.__all__` is exactly the documented public surface in this section plus the error leaves
from PRD 05 and PRD 07 — 38 names, no more, no less. It is derived from three places, not from this
document alone:

1. every `Symbol` row in the §5.3.1–§5.3.5 tables below — 25 names (§5.3.3's second table lists
   *members*, not exports, and §5.3.5 tabulates `DIError` only);
2. the 12 leaves of PRD 05 §5.6.1's `DIError` hierarchy;
3. `OverrideError`, the one new leaf in PRD 07 §7 (absent from the §5.6.1 hierarchy; it appears in
   PRD 05 §5.6.2's catalogue only because PRD 07 amends that catalogue).

"Stability" is `Stable` for names that may not change before 1.0 without a major-version note,
`Provisional` for names whose shape may still move (still covered by deprecation once shipped).

#### 5.3.1 Decorators and declarations

| Symbol | Kind | Signature | Stability | Specified in |
|--------|------|-----------|-----------|--------------|
| `injectable` | decorator factory | `injectable(*args: Any, scope: Scope = Scope.SINGLETON, provides: Sequence[type] \| None = None, multi: bool = False) -> Callable[[T], T]` | Stable | 02 |
| `post_construct` | method decorator | `post_construct(fn: F) -> F` | Stable | 03 |
| `pre_destruct` | method decorator | `pre_destruct(fn: F) -> F` | Stable | 03 |
| `Depends` | declaration marker | `Depends[T] is T` — a spelling-only alias; no marker object and no `Annotated` wrapper is created (D-25). A runtime spelling only: `x: Depends[T]` is not a type expression, so §5.5 states what a typed codebase writes instead | Stable | 04 |
| `Inject` | declaration helper | `Inject(token: Any) -> InjectMarker` | Stable | 04 |
| `OptionalDep` | declaration wrapper | `OptionalDep(token: Any)` | Stable | 04 |

`injectable`'s `*args` is **not** configuration: it exists only so the bare `@injectable` form can be
rejected with `DecoratorUsageError` instead of a `TypeError` (see the §5.4 counter-example). The
reference implementation behaves identically (`_reject_bare_usage` in `lauren/decorators.py`).

#### 5.3.2 Tokens, metadata and provider records

| Symbol | Kind | Signature | Stability | Specified in |
|--------|------|-----------|-----------|--------------|
| `Token` | class | `Token(name: str, *, unique: bool = True)`; props `.name: str`, `.unique: bool` | Stable | 04 |
| `InjectableMeta` | dataclass | `InjectableMeta(scope: Scope = Scope.SINGLETON, provides: tuple[type, ...] = (), multi: bool = False)` | Stable | 02 |
| `injectable_meta` | helper | `injectable_meta(target: Any) -> InjectableMeta \| None` | Provisional | 02 |
| `Provider` | dataclass (read-only by convention) | `Provider(cls, scope, provides, multi, deps, post_construct=None, pre_destruct=None, owning_module=None, factory=None, field_deps=(), is_function_provider=False, is_generator_provider=False, provider_kind=None, alias_target=None, optional_dep_names=frozenset(), positional_arg_names=(), post_construct_hooks=(), pre_destruct_hooks=())` | Provisional field set; names stable | 02 |
| `Owner` | type alias | `Owner: TypeAlias = type \| None` — the module class that declared a row, or `None` for a global row | Stable | 06 |
| `Grant` | type alias | `Grant: TypeAlias = Any \| tuple[Any, type]` — one visible-set entry: a token (all rows bound to it) or a `(token, owner)` row address | Stable | 06 |
| `CustomProvider` | frozen dataclass | `CustomProvider(provide, kind, value=None, use_class=None, factory=None, inject=(), existing=None, scope=Scope.SINGLETON, multi=False, name="")` | Provisional | 04 |
| `OverridePlan` | class | `OverridePlan(providers: Mapping[Any, ProviderOverride] \| None = None)` | Provisional | 07 |
| `ProviderOverride` | frozen dataclass | `ProviderOverride(token, kind, value=None, values=(), use_class=None, factory=None, injects=(), existing=None, scope=None, optional=False)` | Provisional | 07 |
| `building_with` | context manager | `building_with(plan: OverridePlan) -> Iterator[None]` | Provisional | 07 |
| `active_override_plan` | function | `active_override_plan() -> OverridePlan \| None` | Provisional | 07 |

`Provider` also carries two derived properties, `post_construct_chain` and `pre_destruct_chain` (the
hook tuples, falling back to the single `post_construct` / `pre_destruct` field). They are documented
in PRD 02 §5.4 with the rest of the record.

#### 5.3.3 The container and its scopes

| Symbol | Kind | Signature | Stability | Specified in |
|--------|------|-----------|-----------|--------------|
| `DIContainer` | class | `DIContainer(*, overrides: OverridePlan \| None = None, strict_request_scope: bool = False, value_tokens: Iterable[Any] = (), field_predicate: Callable[[str, Any], bool] \| None = None)` | Stable | 02, 08 |
| `Scope` | `IntEnum` | `Scope.TRANSIENT = 0`, `Scope.REQUEST = 1`, `Scope.SINGLETON = 2`; `.label: str` | Stable | 03 |
| `ResolutionScope` | class | `async with container.scope(*, values: Mapping[Any, Any] \| None = None) as scope`; `await scope.resolve(token)`, `.values`, `await scope.close()` | Provisional | 03 |

`DIContainer` methods (all specified across PRDs 02–07):

| Method | Signature | Specified in |
|--------|-----------|--------------|
| `register` | `register(target: type \| Callable[..., Any], *, owning_module: type \| None = None) -> Provider` | 02 |
| `register_value` | `register_value(token: Any, value: Any, *, owning_module: type \| None = None, multi: bool = False) -> Provider` | 04 |
| `register_instance` | `register_instance(token: Any, instance: Any, *, owning_module: type \| None = None) -> Provider` | 03 |
| `register_class` | `register_class(token: Any, cls: type, *, owning_module: type \| None = None, scope: Scope = Scope.SINGLETON, multi: bool = False) -> Provider` | 04 |
| `register_factory` | `register_factory(token: Any, factory: Callable[..., Any], *, inject: tuple[Any, ...] = (), scope: Scope = Scope.SINGLETON, owning_module: type \| None = None, multi: bool = False) -> Provider` | 04 |
| `register_alias` | `register_alias(token: Any, existing: Any, *, owning_module: type \| None = None) -> Provider` | 04 |
| `register_custom` | `register_custom(custom: CustomProvider, *, owning_module: type \| None = None) -> Provider` | 04 |
| `compile` | `compile() -> None` | 05 |
| `has_provider` | `has_provider(token: Any, *, owning_module: type \| None = None) -> bool` | 05 |
| `get_provider` | `get_provider(token: Any, *, owning_module: type \| None = None) -> Provider` | 05 |
| `all_providers` | `all_providers() -> list[Provider]` | 05 |
| `registered_providers` | `registered_providers() -> tuple[Provider, ...]` | 05 |
| `instance_index` | `instance_index() -> Mapping[Any, Provider]` | 03 |
| `applied_overrides` | `applied_overrides() -> frozenset[Any]` | 07 |
| `resolve` | `async resolve(token: Any, *, request_cache: dict[Any, Any] \| None = None, values: Mapping[Any, Any] \| None = None, owning_module: type \| None = None) -> Any` | 02 |
| `scope` | `scope(*, values: Mapping[Any, Any] \| None = None) -> ResolutionScope` | 03 |
| `startup` | `async startup() -> None` | 03 |
| `shutdown` | `async shutdown() -> tuple[DestructError, ...]` | 03 |
| `adopt_lifecycle` | `adopt_lifecycle(instance: Any) -> None` | 03 |
| `set_visible` | `set_visible(module_cls: type, grants: frozenset[Grant]) -> None` | 06 |
| `grant_visible` | `grant_visible(module_cls: type, grants: frozenset[Grant]) -> None` | 06 |
| `visible_in` | `visible_in(module_cls: type) -> frozenset[Any]` | 06 |
| `explain` | `explain(token: Any, *, owning_module: type \| None = None) -> str` | 06 |

`singletons()`, `set_singleton()` and `mark_singleton_initialized()` are **not** public members: they
existed so the framework's scheduler could write back into the container, and D-53 internalises them.
Tests assert cache behaviour through resolution identity and hook firing instead of peeking. In the
other direction, `adopt_lifecycle()` **is** public: it is the embedder's seam for giving an object the
container did not construct a lifecycle, which no other public call can do.

`scope()` is annotated `-> ResolutionScope`, not `-> AsyncContextManager[ResolutionScope]`, because
PRD 03 G3 states the same thing in words ("`container.scope()` returns a `ResolutionScope`") and
because the returned object **is** the context manager: `ResolutionScope.__aenter__` returns `self`
and `__aexit__` closes it, so `async with container.scope() as scope:` is unchanged and it satisfies
`AsyncContextManager[ResolutionScope]` structurally. Naming the protocol instead would be a true but
weaker statement — `scope: ResolutionScope = container.scope(...)` would become a type error, and
`.values`, `.errors` and `await scope.close()`, which §5.3.1's `ResolutionScope` row documents, would
be hidden from the caller. `tests/unit/test_resolve_contract.py` asserts the annotation and this row
in the same words, and `tests/typing/reveal_scope_factory.py` proves both spellings type-check.

#### 5.3.4 Custom-provider recipes

| Symbol | Signature | Specified in |
|--------|-----------|--------------|
| `use_value` | `use_value(*, provide: Any, value: Any, multi: bool = False) -> CustomProvider` | 04 |
| `use_class` | `use_class(*, provide: Any, use: type, scope: Scope = Scope.SINGLETON, multi: bool = False) -> CustomProvider` | 04 |
| `use_factory` | `use_factory(*, provide: Any, factory: Callable[..., Any], injects: Iterable[Any] = (), scope: Scope = Scope.SINGLETON, multi: bool = False) -> CustomProvider` | 04 |
| `use_existing` | `use_existing(*, provide: Any, existing: Any) -> CustomProvider` | 04 |

#### 5.3.5 Errors

| Symbol | Signature | Specified in |
|--------|-----------|--------------|
| `DIError` | `DIError(message: str = "", *, code: str = "lauren_di_error", detail: dict[str, Any] \| None = None)` | 05 |

Every library exception derives from `DIError`; the leaf catalog is PRD 05. Leaves are **exported**
symbols (D-18), so `except MissingProviderError:` is supported while `except DIError:` remains the
catch-all. HTTP-only members of
`lauren`'s `LaurenError` (`status_code`, `headers`, `to_payload`) are **not** carried over. The
framework-only error families (`HTTPError` and descendants, `RouterConflictError`,
`ConfigValidationError`, `SessionConfigError`, `OpenAPISchemaError`, `ImportError`-adjacent
module errors) are not part of this library.

#### 5.3.6 Deliberately not exported

`_InjectMarker`, `framework_value_types`, `register_framework_value`, `OVERRIDE_KINDS` (the raw
`kind` strings are validated but the set itself is internal), `INJECTABLE_META` (the raw attribute
name), and every private helper. `building_with` and `active_override_plan` **are** exported
(PRD 07 §5.5): they are the seam a test helper uses to activate a plan for one build, and a
ContextVar that no user can set is dead weight. Tooling that needs the marker uses `injectable_meta()`; the raw
attribute name is an implementation detail recorded in PRD 08 for migration.

### 5.4 One usage example per symbol

Each block is syntactically complete and is an input to the §10 row 3 test
(`tests/docs/test_prd_examples.py`). Blocks use conventional stand-ins (`Config`, `ConfigService`,
`Connection`, `Cache`, `Driver`, `Repo`, `Logger`, `Clock`, `Unregistered`, `connect`,
`EnvConfigService`, `InMemoryConfig`, …) that the test's `seed_namespace()` defines; no block
depends on a transport or a framework type.

Every block carries the `run` marker and executes in file order in one shared namespace: a later
block may use the imports and declarations of an earlier one, so §5.4 reads as one program. A
block that cannot execute as written must carry `fragment` (or `skip`) instead, and is then only
compiled — the same marker grammar as `scripts/check_docs_contract.py`.

The coverage rule is one `####` subsection per symbol, or per tight pair where the two names describe
one mechanism (`OverridePlan`/`ProviderOverride`, `Owner`/`Grant`,
`building_with`/`active_override_plan`), with one deliberate exception: the thirteen `DIError` leaves
differ only by `code`, so they share the single grouped block at the end of this section, and that
block is their canonical example. The rule is not maintained by hand —
`test_every_exported_name_has_an_example` derives it from `lauren_di.__all__`, so an export that gets
neither a heading nor a place in the group fails, and
`test_the_acceptance_row_states_the_rule_the_section_follows` fails if §10.1's row describes a
different shape than the one this section has.

#### `injectable` — class and function forms

```python run
from lauren_di import Scope, injectable

@injectable()                                  # singleton class provider
class Config:
    dsn = "sqlite:///app.db"

@injectable(scope=Scope.REQUEST)               # request-scoped class provider
class Session:
    def __init__(self, cfg: Config) -> None:
        self.dsn = cfg.dsn

@injectable()                                  # function provider: return value is the dependency
def make_clock() -> "Clock":
    return Clock()

# @injectable          ← rejected: the bare form hides intent
# class Rejected: ...
# raises DecoratorUsageError: write @injectable(), not @injectable
```

#### `DIContainer` — register, compile, resolve

```python run
import asyncio
from lauren_di import DIContainer

async def main() -> None:
    container = DIContainer()
    container.register(Config)
    container.register(Session)
    container.compile()                        # optional: first resolve compiles too
    session = await container.resolve(Session)
    assert session.dsn == "sqlite:///app.db"

asyncio.run(main())
```

#### `Provider` — the record registration returns

```python run
from lauren_di import DIContainer, Scope

container = DIContainer()
container.register(Config)
container.register(Session)
provider = container.get_provider(Session)
assert provider.scope is Scope.REQUEST
assert provider.is_function_provider is False
assert provider.deps == (("cfg", Config),)     # (parameter name, token), in signature order
assert provider.provider_kind is None          # custom-provider kinds are "value"/"class"/"factory"/"existing"
```

#### `InjectableMeta` and `injectable_meta` — reading a declaration

```python run
from lauren_di import Scope, injectable, injectable_meta

@injectable(scope=Scope.REQUEST, provides=[Repo], multi=False)
class SqlRepo:
    ...

meta = injectable_meta(SqlRepo)
assert meta is not None
assert meta.scope is Scope.REQUEST
assert meta.provides == (Repo,)
assert meta.multi is False
assert injectable_meta(str) is None            # primitives are never injectable
```

#### `Scope` — ordered lifetimes

```python run
from lauren_di import Scope

assert Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON   # narrowest → widest
assert Scope.SINGLETON.label == "singleton"

# A wider-scoped consumer may not depend on a narrower-scoped provider:
# singleton → request is a DIScopeViolationError at compile time.
```

#### `Token` — a branded key for non-class providers

```python run
from lauren_di import Token

DB = Token("DB")                 # identity semantics: a fresh Token("DB") is a *different* key
SHARED = Token("SHARED", unique=False)
assert Token("DB") != DB
assert Token("SHARED", unique=False) == SHARED   # explicit opt-in to name equality
```

#### `Inject` — override the token a parameter resolves by

```python run
from typing import Annotated
from lauren_di import Inject, injectable

DB = Token("DB")

@injectable()
class Repository:
    def __init__(self, conn: Annotated[Connection, Inject(DB)]) -> None:
        self.conn = conn

@injectable()
class CachedRepository:
    cache: Annotated[Cache, Inject("CACHE")]      # class-body form works the same way
```

#### `Depends` — explicit DI at a parameter or field

`Depends[T]` is a **runtime** spelling: it evaluates to `T`, so the container receives the bare
token and never a marker object. It is not a *type form* — neither checker accepts it in
annotation position — and §5.5 states the rule a typed codebase follows instead: annotate the
resolved type, or, when the token is not a type at all (a function provider), write the same token
as `Annotated[T, Inject(token)]`, which `Inject` carries and `T` keeps type-checkable.

```python run
from typing import Annotated
from lauren_di import Depends, Inject, injectable

@injectable()
def make_session(cfg: Config) -> Session:
    return Session(cfg.dsn)

# The spelling is *evaluated*, not interpreted: `Depends[X]` is `X` for any X, including a
# function provider — a token no checker would accept as a type argument.
assert Depends[make_session] is make_session
assert Depends[Session] is Session

@injectable()
class UserRepo:
    session: Annotated[Session, Inject(make_session)]   # the factory's token, type-clean

@injectable()
class AuditRepo:
    def __init__(self, users: UserRepo) -> None:  # `Depends[UserRepo]` is `UserRepo`
        self.users = users
```

#### `OptionalDep` — a factory argument that may be absent

```python run
from lauren_di import OptionalDep, use_factory

connection = use_factory(
    provide="CONNECTION",
    factory=lambda cfg, logger: Connection(cfg.dsn, logger),
    injects=[Config, OptionalDep("LOGGER")],   # missing LOGGER → None, not MissingProviderError
)
```

#### `use_value` — bind a literal

```python run
import asyncio
from lauren_di import DIContainer, use_value

container = DIContainer()
container.register_custom(use_value(provide="SETTINGS", value={"debug": True}))

async def main() -> None:
    assert (await container.resolve("SETTINGS"))["debug"] is True

asyncio.run(main())
```

#### `use_class` — bind a token to a different implementation

```python run
from lauren_di import Scope, use_class

provider = use_class(provide=ConfigService, use=EnvConfigService, scope=Scope.SINGLETON)
assert provider.kind == "class" and provider.use_class is EnvConfigService
```

#### `use_factory` — compute the dependency

```python run
from lauren_di import OptionalDep, Scope, use_factory

provider = use_factory(
    provide="CONNECTION",
    factory=connect,
    injects=[Config, OptionalDep("LOGGER")],
    scope=Scope.SINGLETON,
)
assert provider.kind == "factory"
```

#### `use_existing` — alias one token to another

```python run
from lauren_di import use_existing

provider = use_existing(provide="AliasedLogger", existing=Logger)
assert provider.kind == "existing" and provider.existing is Logger
# Both tokens resolve to the same instance; a self-alias raises DecoratorUsageError.
```

#### `post_construct` — setup after construction

```python run
from lauren_di import injectable, post_construct

@injectable()
class Pool:
    @post_construct
    async def open(self) -> None:                  # runs once per instance, before first use
        self.conn = await connect(self.dsn)
```

#### `pre_destruct` — teardown at scope exit

```python run
from lauren_di import Scope, injectable, pre_destruct

@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    @pre_destruct
    async def rollback(self) -> None:              # runs on scope exit, newest instance first
        await self.tx.rollback()
```

#### `DIError` — one catchable base

```python run
import asyncio
from lauren_di import DIContainer, DIError

async def main() -> None:
    try:
        await DIContainer().resolve(Unregistered)
    except DIError as exc:
        print(exc.code, exc.detail)    # e.g. "missing_provider" {"token": "Unregistered"}

asyncio.run(main())
```

#### `OverridePlan` / `ProviderOverride` — test-time substitution

```python run
import asyncio
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Scope

plan = OverridePlan(providers={
    ConfigService: ProviderOverride(
        token=ConfigService, kind="value", value=InMemoryConfig()
    ),
})

async def main() -> None:
    container = DIContainer(overrides=plan)
    container.register(ConfigService)      # an override replaces a registration (OV-15)
    container.compile()
    assert isinstance(await container.resolve(ConfigService), InMemoryConfig)

asyncio.run(main())
```

#### `CustomProvider` — the record the recipes return

```python run
from lauren_di import use_value

custom = use_value(provide="FLAG", value=True)
assert custom.kind == "value" and custom.value is True and custom.name == "useValue"
```

#### `ResolutionScope` — the object `scope()` hands back

`container.scope()` returns the scope itself, not a bare context-manager protocol, so the
same object works both ways: entered with `async with`, or held and closed explicitly.

```python run
import asyncio
from lauren_di import DIContainer, ResolutionScope

async def main() -> None:
    container = DIContainer()
    scope: ResolutionScope = container.scope()      # the factory returns the scope itself
    async with scope as opened:
        assert opened is scope                      # __aenter__ returns self
    print(scope.closed, scope.errors)               # True ()

asyncio.run(main())
```

#### `Owner` / `Grant` — the visibility vocabulary

`Owner` is the module class a row belongs to (`type | None`, `None` meaning app-level). `Grant` is
one entry in a module's visible set: a bare token, or a `(token, owner)` pair addressing exactly one
row. Both are type aliases, so `frozenset({Logger})` needs no wrapper.

```python run
from lauren_di import DIContainer, Grant, Owner

class RepoModule:
    pass

container = DIContainer()
grants: frozenset[Grant] = frozenset({Logger, (Cache, RepoModule)})
container.set_visible(RepoModule, grants)

owner: Owner = RepoModule                           # `type | None`
assert container.visible_in(RepoModule) >= {Logger, Cache}
print(owner.__name__, sorted(t.__name__ for t in container.visible_in(RepoModule)))
```

#### `building_with` / `active_override_plan` — the override context

`building_with(plan)` activates a plan for the container build inside the block, which is how a
caller applies overrides to a container it does not construct itself. `active_override_plan()`
reads it back, and returns `None` outside the block.

```python run
import asyncio
from lauren_di import (
    DIContainer, OverridePlan, ProviderOverride, active_override_plan, building_with,
)

plan = OverridePlan(providers={
    ConfigService: ProviderOverride(token=ConfigService, kind="value", value=InMemoryConfig()),
})

with building_with(plan):
    assert active_override_plan() is plan
    container = DIContainer()                       # the build picks the active plan up
    container.register(ConfigService)
    container.compile()
    assert isinstance(asyncio.run(container.resolve(ConfigService)), InMemoryConfig)

assert active_override_plan() is None
```

#### The thirteen error leaves — one family, one `except`

`DIError` above is the base every failure raises. The leaves exist so a caller can catch narrowly,
but each keeps a distinct `code` and remains catchable as `DIError`, so the grouped form below is
the canonical example for all thirteen rather than thirteen near-identical blocks.

```python run
from lauren_di import (
    CircularDependencyError, DIError, DIScopeViolationError, DecoratorUsageError,
    DestructError, DuplicateBindingError, InvalidProviderError, LifecycleConfigError,
    LifecycleViolationError, MetadataInheritanceError, MissingProviderError, OverrideError,
    ProtocolAmbiguityError, UnresolvableParameterError,
)

leaves = [
    CircularDependencyError, DIScopeViolationError, DecoratorUsageError, DestructError,
    DuplicateBindingError, InvalidProviderError, LifecycleConfigError,
    LifecycleViolationError, MetadataInheritanceError, MissingProviderError, OverrideError,
    ProtocolAmbiguityError, UnresolvableParameterError,
]
assert all(issubclass(leaf, DIError) for leaf in leaves)   # one `except DIError` catches all
assert len({leaf.code for leaf in leaves}) == 13           # each leaf keeps its own code
print(sorted(leaf.code for leaf in leaves))
```

### 5.5 Import policy and stability

- `from lauren_di import X` is the only documented import form. Submodule paths
  (`lauren_di._internal`) are not part of the API.
- `__all__` is normative and sorted; a test asserts it equals §5.3 exactly (no undocumented
  exports, no missing names).
- **`Depends[T]` is a runtime spelling, not a type form.** It evaluates to `T` (§5.4), which is what
  makes it a spelling rather than a marker (D-25) — but a subscripted *value* is not a type
  expression, so `x: Depends[T]` does not type-check: mypy reports
  `Variable "Depends" is not valid as a type [valid-type]` and pyright
  `reportInvalidTypeForm: Variable not allowed in type expression`. Making `Depends` a class does
  not fix it, and this is the reason the spelling is documented as runtime-only rather than
  repaired: `Depends[T]` would then *mean* `Depends[T]`, so `repo.session.dsn` becomes an attribute
  error on the wrapper, and a function provider is not a valid type argument at all
  (`Function "make_session" is not valid as a type`). A typed codebase annotates the resolved type
  — `session: Session`, the same token the spelling produces — and writes the spelling only where
  it is *evaluated*, e.g. `assert Depends[Session] is Session`. When the token is not a type at all,
  the type-clean spelling of that same token is `Annotated[T, Inject(token)]`: `Inject` carries the
  key and `T` keeps its static type, so a function provider is written
  `session: Annotated[Session, Inject(make_session)]` rather than `session: Depends[make_session]`.
  §5.4's example is written both ways; `tests/docs/test_depends_spelling.py` fails if *any* python
  block in the corpus puts the spelling in annotation position (the whole of `prds/` and every file
  the docs contract reads, not just §5.4), and `tests/unit/test_public_api.py` fails if this bullet
  stops stating the rule.
- **Stable** names may be added to within a minor release but not renamed or removed before a
  major release, with the exception of `Provider`'s field set (additive only) and the error
  leaves (additive only).
- **Provisional** names must carry a docstring note and appear in the changelog under
  "Provisional API" until frozen at 1.0.0.
- `Provider` is a **value record**: the container may finalise lifecycle fields during
  registration, but consumers must treat every field as read-only. Mutation by user code is
  unsupported and not detected.

### 5.6 Typing contract

- `resolve` carries overloads so the common shapes type-check without casts:

```python fragment
from typing import Any, Mapping, TypeVar, overload

from lauren_di import Token

T = TypeVar("T")

class DIContainer:
    @overload
    async def resolve(
        self,
        token: type[T],
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> T: ...
    @overload
    async def resolve(
        self,
        token: str | Token,
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> Any: ...

    async def resolve(
        self,
        token: Any,
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> Any:
        ...  # implementation
```

- The fallback overload names the two **non-class** token kinds — a `str` and a `Token` — and not
  `Any`. An overloaded call is resolved with the assignment's declared type in view, and `Any`
  matches a class token too, so with an `Any` fallback `number: int = await
  container.resolve(Config)` took the untyped variant and passed `mypy --strict` while `pyright`
  rejected the same line. Naming the non-class kinds leaves a class token exactly one candidate, so
  both checkers report the conflict instead of one of them absorbing it.
- The implementation signature stays `token: Any` — that is the row §5.3.3 states. The overloads are
  declaration-only, so the runtime still accepts **any** hashable key; only the static surface
  narrows. A host that resolves an exotic key (or a token whose static type mixes a class with a
  string, e.g. `tok: type[Config] | str` in a registration loop) writes
  `await container.resolve(cast(Any, key))`, which is the one spelling this contract costs.
- `tests/unit/test_resolve_contract.py` pins all three halves: the pair's spelling in the source and
  in this block, the `reveal_type` inference a class token keeps when nothing conflicts, and the
  rejection of `number: int = await container.resolve(Config)` by a checker run.
- `Depends[T]` and `Inject(token)` live in annotations; the resolved value keeps the annotated
  type for static checkers even when the runtime token is a string or `Token`.
- `py.typed` is shipped; the typing test suite runs `mypy --strict` and `pyright` over the §5.4
  examples and the public stubs.

## 6. Architecture Fit & Feasibility

### 6.1 Extracted, adapted, dropped, replaced

| Reference (`lauren/_di`, `lauren/decorators.py`, `lauren/types.py`) | In `lauren-di` | Note |
|---------------------------------------------------------------------|----------------|------|
| `DIContainer`, `Provider`, `InjectableMeta`, `injectable` | Extracted unchanged in behaviour | D-2 parity contract |
| `Scope` + `.label` | Extracted | Kept as an ordered `IntEnum`; ordering is the narrowing rule |
| `Token`, `Inject`, `OptionalDep`, `CustomProvider`, `use_*` | Extracted | D-2 |
| `@post_construct`, `@pre_destruct` | Extracted | Fired by the container (D-7) |
| `Depends` | Adapted | Stays a DI declaration marker; loses `ExtractionMarker` base and HTTP meaning |
| `framework_values` + `Request` special case | **Replaced** by `values: Mapping[Any, Any]` | D-8; PRD 08 mapping |
| `_is_extractor_marker` in field collection | **Replaced** by a pluggable predicate | D-9; PRD 02 |
| `LifecycleScheduler` | **Replaced** by `container.startup()` / `shutdown()` | D-7; PRD 03 |
| `set_visible` / `owning_module` | Carried, **active in v1**; the module graph is not carried | D-37 (resolves D-5); PRD 06 |
| `OverridePlan.modules` | Dropped (no module graph) | D-12, D-44 |
| `StartupError`, `LaurenError` HTTP fields | **Replaced** by `DIError` | PRD 05 |
| `lauren.__init__`'s 500-line re-export surface | Dropped | `lauren-di` exports §5.3 only |

### 6.2 Import graph

`lauren_di` imports only the standard library:

```text
lauren_di/__init__.py
├── _container.py      (DIContainer, Provider, compile/resolve)
├── _provider.py       (Provider, InjectableMeta, injectable, injectable_meta)
├── _custom.py         (Token, Inject, OptionalDep, CustomProvider, use_*)
├── _scope.py          (Scope, ResolutionScope, teardown)
├── _introspect.py     (signature + annotation collection)
├── _overrides.py      (OverridePlan, ProviderOverride)
├── _errors.py         (DIError and leaves)
└── py.typed
```

No module may import `lauren`, `pydantic`, `msgspec`, `anyio`, `httpx` or `click`. That denylist is
absolute: wrapping one of the six in a `try:`/`except ImportError:` block does not make it allowed.
Otherwise a test walks the import graph of the built wheel and fails on any **mandatory**
third-party import (G4).

**Guarded optional imports:** `typing_extensions` — consulted only if it is already installed, never
required. This is the only third-party name a guarded import may mention, and it is declared here on
purpose: `tests/packaging/test_no_framework_imports.py` reads this line, so a guarded import that is
not named above fails, and an allowance that outlives its import fails as well. It exists because
`lauren_di/_typing/forwardref.py` is kept byte-comparable with the standalone resolver it was
vendored from, and that resolver widens its namespace with `typing_extensions` when a user happens to
have it installed; deleting the branch would cost that property and buy nothing, since the wheel
imports without it (§5.1, G4).

### 6.3 The three seam replacements

Each framework coupling becomes a generic API, specified fully in the PRD named:

1. **Runtime values** → `resolve(..., values={SomeType: instance})` and
   `container.scope(values=...)`. The container treats keys as opaque type-like tokens; `Request`
   is simply a type an embedder may use (D-8, PRD 03).
2. **Field markers** → a predicate hook (e.g. `DIContainer(field_predicate=...)`) or, in the
   minimal version, recognition of `Depends[...]` only. PRD 02 fixes the default; PRD 08 shows
   how `lauren` passes its extractor predicate (D-9).
3. **Singleton hooks** → `await container.startup()` runs `@post_construct` in dependency order;
   `await container.shutdown()` runs `@pre_destruct` in reverse (D-7, PRD 03).

### 6.4 Feasibility

- **Behavioural parity is mechanical.** The reference implementation is ~3,000 lines across four
  files with no framework imports beyond `_typing`, `types.Scope`, `exceptions` and
  `extractors`. Everything except the three seams in §6.3 moves unchanged; PRD 02 carries a
  function-by-function parity table.
- **3.10 support is real, not aspirational.** The code paths use `inspect.signature`,
  `inspect.get_annotations` (3.10+), `typing.get_origin`/`get_args`, `dataclasses`, `enum`,
  `contextvars` and `asyncio`. The forward-reference resolver is in-tree precisely so no
  `typing_extensions` dependency is needed.
- **Async-first costs nothing for sync users.** `resolve` is `async`, but a provider whose
  factory is synchronous never awaits anything except the (already-resolved) dependency
  futures. PRD 02 specifies the exact await points; PRD 03 specifies that hooks may be sync or
  async.
- **Performance is inherited.** Compilation is a single DFS with colour marking; resolution is
  dict lookups plus `inspect.signature` results cached in `Provider`. No new hot-path work is
  introduced by the packaging change.
- **Risk: the `values` rename.** Renaming `framework_values` → `values` touches every call site
  in `lauren`. PRD 08 makes the rename one mechanical change and keeps a compatibility keyword
  in an intermediate release if needed (D-13).

### 6.5 Why a separate distribution rather than an optional extra

`lauren[di]` would still install the framework, still resolve its import graph, and still let DI
code import `lauren` — the coupling this PRD removes. A separate distribution also gives
embedders a version to constrain (`lauren-di>=0.1,<1`) and lets the DI library release on its own
cadence. The precedent already exists in this repository: `lauren-middlewares`, `lauren-guards`,
`lauren-logging` and `lauren-mcp` are separate distributions with their own metadata and docs.

## 7. Error Catalog Impact

This PRD introduces the error *root*, not the catalogue:

| Change | Detail |
|--------|--------|
| New base | `DIError(Exception)` with `code: str`, `message: str`, `detail: dict[str, Any]`; `__str__` returns `message` |
| New leaf | `InvalidProviderError` (`code="invalid_provider"`): `register()` received neither a class nor a callable (PRD 02, D-16) |
| Dropped | `LaurenError.status_code`, `.headers`, `.to_payload()` — HTTP response concerns |
| Renamed | `StartupError` is not exported; build-time failures become `DIError` leaves in PRD 05 |
| Unchanged in meaning | `MissingProviderError`, `DuplicateBindingError`, `CircularDependencyError`, `DIScopeViolationError`, `ProtocolAmbiguityError`, `UnresolvableParameterError`, `MetadataInheritanceError`, `DestructError`, `DecoratorUsageError`, `LifecycleConfigError` |
| Not carried | `HTTPError` and its ~30 descendants, `RouterConflictError`, `CircularModuleError`, `ModuleExportViolation`, `ConfigValidationError`, `SessionConfigError`, `OpenAPISchemaError`, `MiddlewareConfigError`, `GuardConfigError`, `InterceptorConfigError`, `ExceptionHandlerConfigError`, `DestructTimeoutError`, `DrainTimeoutError` |
| Carried after review | `LifecycleViolationError` (`code="lifecycle_violation"`), first considered dropped: a single-use `ResolutionScope` needs a guard against use outside its lifetime, so D-23 re-activates the leaf. Trigger, `detail` and message shape in PRD 05 row 28; usage in PRD 03 §5.3 |
| Requirement | Every leaf is a direct or indirect subclass of `DIError`; a leaf named `…Error` is either raised or, for `DestructError`, returned as a documented value (D-35, PRD 03 D-20) |

PRD 05 owns codes, message shapes, `detail` keys and the "raised by" mapping.

## 8. Phased Implementation Plan

| Phase | Version | Contents | Exit criteria |
|-------|---------|----------|---------------|
| 1 — Core vocabulary | `0.1.0` | Package skeleton, `__all__`, `Scope`, `injectable`, `injectable_meta`, `Provider`, `DIContainer.register/resolve`, constructor + field injection, `DIError`; §5.2 quickstart runs | G1–G6 pass; parity suite for PRDs 02/03/05 green |
| 2 — Lifetimes | `0.1.x` | `container.scope()`, strict/lax request scope, hooks, `startup()`/`shutdown()`, teardown | PRD 03 state table covered by tests |
| 3 — Binding surface | `0.2.0` | `Token`, `Inject`, `Depends`, `OptionalDep`, `use_*`, multi-bindings, aliases | PRD 04 matrix green; examples in §5.4 execute |
| 4 — Overrides & visibility | `0.3.0` | `OverridePlan`/`ProviderOverride`; module visibility active, per D-37 | PRD 06/07 green |
| 5 — Freeze | `1.0.0` | Deprecation policy, typing completeness, docs contract, llms artefacts, changelog | Stability promises in §5.5 enforceable |

Phase 1 does not wait for the release scaffolding, but **publishing** does: goal #11 supplies
`noxfile.py`, CI, `llms.txt`/`llms-full.txt`, `CHANGELOG.md`, `AGENTS.md`/`CLAUDE.md` and the
final `pyproject.toml`.

## 9. Documentation & Convention Updates

| Artefact | Update |
|----------|--------|
| `README.md` | §5.2 quickstart verbatim; one-paragraph boundary statement; install line; link to PRD set |
| `docs/index.md` | "What this library is / is not" copied from §1 and §3 |
| `docs/getting-started.md` | Install, first container, first provider, first scope |
| `docs/guides/providers.md` | Class, function, field and constructor injection (PRD 02) |
| `docs/guides/scopes.md` | Scope matrix, strict mode, teardown (PRD 03) |
| `docs/guides/custom-providers.md` | The four recipes and `list[T]` (PRD 04) |
| `docs/guides/testing.md` | Overrides and fixtures (PRD 07) |
| `docs/reference.md` | Generated from `__all__` + docstrings; must match §5.3 exactly |
| `llms.txt` / `llms-full.txt` | Full API corpus; `llms_check` fails if any `__all__` symbol is unreferenced |
| `AGENTS.md` / `CLAUDE.md` | Library conventions: stdlib-only, no HTTP vocabulary, `__all__` discipline, parity rule |
| `CHANGELOG.md` | Keep-a-Changelog; `0.1.0` entry lists G1–G6 as delivered |
| `CONTRIBUTING.md` | "Is this DI?" boundary test from §3, parity requirement, docs-contract command |

Convention changes recorded for later PRDs:

1. Every public name in §5.3 must appear in `__all__`, `docs/reference.md` and
   `llms-full.txt`; the same three-way check applies to every future addition.
2. Every example in PRD 02–07 must compile, and every block carries a marker: `run` means a
   reader is meant to type it, and `tests/docs/test_prd_examples.py` executes every `run` block in
   the corpus; `fragment` (or `skip`) means deliberately partial, compiled only.
   `prds/check_consistency.py`'s `blocks` check fails on a block that does not compile or carries
   no marker, so pseudo-code is a stated choice rather than an unmarked default.
3. No PRD may add an HTTP, routing, extractor, config, scheduling or module-graph concept to the
   public surface (README §3).

## 10. Test Plan

| Layer | Location | Covers |
|-------|----------|--------|
| Public surface | `tests/unit/test_public_api.py` | `__all__` equals §5.3 exactly; no private names leak; every name imports |
| Quickstart | `tests/integration/test_quickstart.py` | §5.2 runs unmodified and prints the asserted value |
| Examples | `tests/docs/test_prd_examples.py` | Every §5.4 block compiles; every `run` block in `prds/*.md` executes (28 of 88; the rest are marked `fragment`) |
| Packaging | `tests/packaging/test_wheel.py` | `py.typed` in wheel; no *unconditional* `Requires-Dist` — every `Requires-Dist` is `extra ==`-gated; `requires-python >=3.10`; import works from a clean venv |
| Import graph | `tests/packaging/test_no_framework_imports.py` | Built wheel has no *mandatory* third-party import — the only guarded optional import is the one §6.2 declares; `lauren` absent |
| Typing | `tests/typing/` | `mypy --strict` and `pyright` over §5.4 and public stubs; `resolve` overloads infer `T` |
| Interpreters | CI matrix | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Boundary | `tests/unit/test_non_goals.py` | `lauren_di` exports no HTTP/router/extractor/scheduler/module-graph symbol (forbidden-name denylist) |
| Parity | `tests/parity/` | Behaviour identical to `lauren/_di` for the shared surface (PRD 08 matrix) |

Property tests worth writing at 0.1.0: scope-narrowing matrix (all nine consumer/dependency
pairs), cycle detection over generated graphs, and `list[T]` membership order.

### 10.1 Acceptance traceability for this PRD

| Acceptance clause | Where satisfied |
|-------------------|-----------------|
| Positioning: DI-only, no ASGI/routing/config/CLI | §1, §3, §6.5 |
| Distribution `lauren-di`, import `lauren_di` | §5.1 |
| Stdlib-only, Python `>=3.10`, PEP 561 | §5.1, G4–G6, §6.2, §10 packaging/import rows |
| Complete exported-symbol table with signatures | §5.3.1–§5.3.6 |
| One usage example per named symbol | §5.4 — one `####` subsection per symbol or tight pair, plus one grouped block shared by the thirteen `DIError` leaves |
| 20-line quickstart using `@injectable` + `await …resolve(...)` | §5.2 |
| Non-goals cover HTTP request objects, routing, extractors, scheduling, module-graph compiler | §3 (first five bullets) |
