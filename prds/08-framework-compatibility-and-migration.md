# PRD 08 — Framework Compatibility & Migration

**Status:** Draft — 2026-11-21
**Depends on:** PRDs 01–07
**Resolves:** D-10 (migration aliasing/deprecation policy), D-11 (marker names), D-13 (`framework_values` rename); adds D-50…D-55
**Related:** `prds/README.md` §3 (product boundary), §4 (decision register)
**Probes:** `E-401` … `E-436` against `../lauren-framework` on Python 3.13, imported with the checkout pinned; indexed in §6.5

---

## 1. Problem Statement

`lauren-di` is an extraction, not a rewrite: PRD 01 fixes the vocabulary (`injectable`, `Scope`,
`Token`, `Inject`, `Depends`, `use_*`, `DIContainer`, `Provider`) and PRD 02 keeps all 18 `Provider`
fields, so a user moving a service layer out of `lauren` expects a rename, not a redesign. That
expectation is only safe if someone writes down **exactly which lines of the reference are DI and
which are framework**, because the DI module currently reaches into the framework in four places and
a naive extraction would carry the reach with it.

### 1.1 The four reach-ins, measured

| # | Reach-in | Where | What it costs |
|---|----------|-------|---------------|
| R-1 | The request value | `DIContainer.resolve(..., framework_values=...)`; `compile()` hard-codes `Request` in its skip set (`_di/__init__.py:1118–1120`) | The container's public signature names an HTTP request |
| R-2 | The extractor markers | `_is_extractor_marker()` imports `lauren.extractors.ExtractionMarker` **inside** `_di` (`:2313–2331`); `_collect_field_deps` accepts any marker | `Path`/`Header` on a class field make the *container* demand a `str` provider |
| R-3 | The lifecycle scheduler | `LifecycleScheduler` lives in `lauren/_lifecycle/`; the container exposes `adopt_lifecycle`, `mark_singleton_initialized`, `set_singleton` **for it** | The DI public API is shaped by a consumer that is not shipped |
| R-4 | The global value registry | `_FRAMEWORK_VALUES` in `_di/_scope.py`, filled by `register_framework_value()` at import time in scheduling and commands | Process-global mutable state that no test can undo |

R-1 and R-4 are one concern (where do out-of-band values come from); R-2 and R-3 are the two
framework subsystems that assume the container.

### 1.2 The two that are only naming

`INJECTABLE_META = "__lauren_injectable__"` and the two hook markers
(`__lauren_post_construct__`, `__lauren_pre_destruct__`) are string attributes. They are not
reach-ins, but they encode the framework's name in every decorated class, so a DI library that keeps
them ships an advertisement for a dependency it does not have (D-11).

### 1.3 The export gap runs the other way

The reference exports **fewer** names than the library needs. `lauren.__all__` has 295 entries and
binds `DIContainer`, but `Provider`, `InjectableMeta`, `CustomProvider` and `INJECTABLE_META` are not
in it (probes `E-402b`…`E-402e`); only `lauren._di.__all__` names the first three (`E-402g`). A user
who writes `from lauren import Provider` gets `ImportError`. PRD 01 exports all three at the top
level, so the migration is *additive* there — no alias is required, because there was nothing to
alias.

### 1.4 The question this PRD has to answer

D-10 is still `Open`: *"Migration aliasing/deprecation policy for `lauren` users — options:
rename-only, temporary re-export shims, or a documented codemod."* Leaving it open means the library
cannot state a version policy, and every compatibility question becomes a judgement call at review
time. §5.8 closes it.

### 1.5 Evidence method, and why the pin matters

Every observation in this PRD was produced by a script in `/tmp/prd08/` run with
`PYTHONPATH=/root/python_projects/lauren-all/lauren-framework`. The pin is not decoration: this
environment also has **`lauren` installed** at
`/root/python_projects/agenthicc/.venv/lib/python3.13/site-packages/lauren`, and that build is a
different artifact — 2058 lines in `_di/__init__.py` against the checkout's 2478, with
`instance_lifecycle_hooks` present only in the checkout. A probe run from a directory that is not on
`sys.path` imports the installed copy and silently measures the wrong code. The runner requirement is
recorded as its own goal; §6.5 states the checksum-bearing rule the harness must follow.

---

## 2. Goals

| # | Goal | Acceptance |
|---|------|------------|
| G1 | Every reference coupling has a named replacement or an explicit "not supported" with a reason | §5.1's C-1…C-24 table has no blank cell |
| G2 | Symbol-by-symbol mapping is complete enough to migrate a file mechanically | §5.3 covers imports, exports, markers, container methods and exceptions |
| G3 | The three HTTP-bound seams get generic replacements | §5.4 (`values`), §5.5 (`field_predicate`), §5.6 (`startup`/`shutdown`) |
| G4 | A parity strategy that runs against **both** implementations | §5.9's adapter harness, prototyped and green on the reference (`E-430`…`E-433`) |
| G5 | A parity matrix naming invariant, fixture and expected outcome | §5.10, P-1…P-12 |
| G6 | D-10 is decided, with a removal schedule | D-50, §5.8 |
| G7 | Marker-attribute rename decided and recorded | D-51, MC-01…MC-03 |
| G8 | No new contradiction with PRDs 01–07 | §6.2 lists the two amendments, both recorded in their owning PRD |
| G9 | The library never depends on the framework | MC-23, T08-23 |
| G10 | Nothing claims behaviour the probes did not measure | §6.5 indexes every cited id to a script |

---

## 3. Non-Goals

- **Porting the framework.** No routes, guards, interceptors, middleware, extractors, WebSockets,
  OpenAPI, config loading, commands or scheduling. Those are PRD 01 §3's non-goals; this PRD only
  names their DI-facing seams and refuses them.
- **A `lauren` shim inside `lauren_di`.** The compatibility layer, if it exists at all, lives in the
  framework (D-50). `lauren_di` never imports `lauren`, not even behind `try/except`.
- **Byte-identical behaviour where the reference is buggy.** Four reference defects are already
  decisions in earlier PRDs (D-45…D-49); parity means "the same *invariant* holds", not "the same
  bug reproduces". §5.10's divergence register is the authority.
- **Automatic migration of a whole codebase.** §5.11 specifies a codemod for the mechanical part
  (imports and marker strings) and states what it will not touch.
- **A second test suite for the framework.** The parity suite parametrises; it does not re-test
  `lauren` on the framework's own terms.
- **Deprecation shims inside `lauren` itself.** That repository owns its release process; this PRD
  specifies the contract it may adopt, not its implementation timeline.

---

## 4. Prior Art

| Source | What is borrowed | What is rejected |
|--------|------------------|------------------|
| `flask` → `quart`, `requests` → `httpx` | A one-release overlap window where both names import, then removal | Keeping the old name forever under an alias |
| `pydantic` v1→v2 `pydantic.v1` | A frozen copy of the old API behind a submodule | Copying code: here the *new* library is the source of truth |
| `attrs` → `attr` / `importlib.metadata` | Thin re-export modules with `__getattr__` and a `DeprecationWarning` | Runtime `__getattr__` magic on the hot path |
| NestJS `@nestjs/core` split | DI as its own package consumed by the framework | The module graph (D-37 keeps it out) |
| `dependency-injector` | A documented "migrating from" page with a symbol table | Provider-as-DSL objects |
| Python's own `distutils` removal (PEP 632) | A dated removal schedule with an environment flag to keep the old path alive | Indefinite support |

The shape that fits: the library is the destination and is clean; the framework may keep a thin,
dated compatibility module. Precedent for the direction — `attrs` never imports `attr`'s users.

---

## 5. Proposed API

### 5.1 Coupling inventory

Every coupling the reference exposes, with its disposition. "Replacement" means the library has a
named thing that does the same job; "not supported" means the capability is refused, with the reason
it cannot exist in a DI-only library.

| # | Coupling | Reference location | Disposition | Where |
|---|----------|--------------------|-------------|-------|
| C-1 | `__lauren_injectable__` attribute | `_di/__init__.py:101` (`INJECTABLE_META`) | **Replacement:** rename to `__lauren_di_injectable__` | D-51, MC-01 |
| C-2 | `__lauren_post_construct__` | `decorators.py:68` | **Replacement:** rename to `__lauren_di_post_construct__` | D-51, MC-02 |
| C-3 | `__lauren_pre_destruct__` | `decorators.py:69` | **Replacement:** rename to `__lauren_di_pre_destruct__` | D-51, MC-03 |
| C-4 | `InjectableMeta(scope, provides, multi)` payload | `_di/__init__.py:181` | **Unchanged** — same three fields | MC-04 |
| C-5 | `lauren._di` private module path | `lauren/_di/` | **Replacement:** `lauren_di` package root; no `_di` submodule | MC-05 |
| C-6 | `Scope` in `lauren.types` | `types.py:40` | **Replacement:** `lauren_di.Scope`, values 0/1/2 unchanged | MC-06 |
| C-7 | `injectable` / `post_construct` / `pre_destruct` in `lauren.decorators` | `decorators.py:88,761,769` | **Replacement:** exported from `lauren_di` | MC-07 |
| C-8 | `resolve(..., framework_values=...)` | `_di/__init__.py` `resolve` | **Replacement:** `resolve(..., values=...)` | D-13, D-52, MC-08 |
| C-9 | Global `_FRAMEWORK_VALUES` + `register_framework_value()` | `_di/_scope.py:32–52` | **Replacement:** per-container `DIContainer(value_tokens=…)` | D-52, MC-09 |
| C-10 | `Request` hard-coded in the compile skip-set | `_di/__init__.py:1118–1120` | **Replacement:** no hard-coded type; `Request` is only a declared value token | MC-10 |
| C-11 | Missing value surfaces mid-construction | measured `E-412` | **Replacement:** fail at scope entry, before construction | MC-11 |
| C-12 | `_is_extractor_marker()` imports `lauren.extractors` | `_di/__init__.py:2313–2331` | **Replacement:** optional `field_predicate` callable | D-54, MC-12 |
| C-13 | `Depends` is an `ExtractionMarker` subclass | `extractors.py:315` | **Replacement:** `Depends[T]` is `T`, a spelling alias | D-25, MC-13 |
| C-14 | `Path`/`Header`/`Query`/`Cookie`/`Json`/`Form`/`State`/`Bytes`/`UploadFile`/`ByteStream`/`HostParam` collected as DI fields | `extractors.py:264–374`; measured `E-404`, `E-405` | **Not supported** — an HTTP parameter source has no meaning without a request | MC-14 |
| C-15 | Extractor module drags `pydantic` into the import path | defensive local import at `:2313` | **Replacement:** the predicate seam is stdlib-only by contract | MC-15 |
| C-16 | `LifecycleScheduler.compute_order` / `run_post_construct` / `run_pre_destruct` | `_lifecycle/__init__.py:29–118` | **Replacement:** `container.startup()` / `container.shutdown()` | D-53, MC-16 |
| C-17 | Scheduler write-back: `mark_singleton_initialized`, `set_singleton`, `singletons` (plus `adopt_lifecycle`, retained) | measured `E-426` | **Replacement:** the two write-back hooks and `singletons` are internalised; `adopt_lifecycle()` and `instance_index()` stay public | D-56, MC-17 |
| C-18 | `instance_lifecycle_hooks()` helper | `_di/__init__.py:1648`; measured `E-421` | **Replacement:** `Provider.post_construct_chain` / `pre_destruct_chain` | MC-18 |
| C-19 | Hooks fire only when the scheduler runs | measured `E-420`, `E-423` | **Replacement:** PRD 03's scope split (REQUEST at resolve, SINGLETON at startup) | MC-19 |
| C-20 | `LaurenError` → `StartupError` two-level base | `exceptions.py` | **Replacement:** one `DIError` base | MC-20 |
| C-21 | 46 exception classes, most of them HTTP | measured: 16 `HTTPError` subclasses, 30 startup/config classes | **Not supported:** the HTTP half is not exported | MC-21 |
| C-22 | No `OverrideError`; no `InvalidProviderError` | — | **Additive:** library-only classes with no reference counterpart | MC-22 |
| C-23 | `lauren` is a dependency of the DI code | — | **Forbidden:** dependency direction is one-way | MC-23 |
| C-24 | No parity suite, no shared invariants | — | **Replacement:** adapter-parameterised harness | MC-28…MC-33 |

Two consequences worth stating outright:

- **C-14 is the only "not supported" that changes a working program.** A class that annotates a field
  `q: Annotated[str, Query]` compiles under `lauren` (it becomes a `str` dependency and then fails:
  `E-404`), and under `lauren_di` it is simply not a DI field. That is a *behaviour change*, and §5.11's
  codemod flags it rather than silently dropping the annotation.
- **C-17 removes three public members.** `set_singleton`, `mark_singleton_initialized` and `singletons`
  are public today only because the scheduler needs them (they appear in `dir(container)` — `E-426`).
  A DI-only container that owns `startup()`/`shutdown()` has no caller for them. `adopt_lifecycle` is
  **kept**: embedders use it to give an object the container did not construct a lifecycle, which is
  not scheduler plumbing (D-56).

### 5.2 Marker renames

The marker is the only thing a decorated object carries, so the rename is a data migration, not an
API one: every `@injectable()` class in a user's codebase changes one attribute name at import time.

```python
# lauren (before)
class OrderService:
    pass

assert vars(OrderService) == {}          # undecorated
OrderService = injectable()(OrderService)
assert "__lauren_injectable__" in vars(OrderService)

# lauren_di (after)
OrderService = injectable()(OrderService)
assert "__lauren_di_injectable__" in vars(OrderService)
assert "__lauren_injectable__" not in vars(OrderService)

# Both versions read the marker through the helper, never the raw string:
meta = injectable_meta(OrderService)     # -> InjectableMeta | None
```

- **MC-01.** `injectable()` sets exactly one attribute, `__lauren_di_injectable__`, holding an
  `InjectableMeta`. It sets no other `__lauren*` attribute, so `vars(cls)` is unchanged apart from
  that key (`E-401d`, `E-401e`) (T08-01).
- **MC-02.** `post_construct()` sets `__lauren_di_post_construct__ = True` on the function; the
  reference's value is `__lauren_post_construct__` (`E-401b`) (T08-02).
- **MC-03.** `pre_destruct()` sets `__lauren_di_pre_destruct__ = True`; reference value
  `__lauren_pre_destruct__` (`E-401c`) (T08-03).
- **MC-04.** The payload dataclass keeps the reference's three fields — `scope`, `provides`, `multi`
  (`E-402i`) — and gains nothing. New metadata is additive on the marker, never a fourth field
  (T08-04).
- **MC-05.** The public import path is the package root. `lauren_di` exposes every name in §5.3.1
  directly; there is no public `_di` path to import from, and a user who wrote
  `from lauren._di import …` writes `from lauren_di import …`. Internal module splits
  (`_scope`, `_overrides`) may exist, but they are not part of the surface. The record the root
  exposes keeps its shape too: `Provider` is the reference's 18 fields, name for name (§5.3.3),
  so a future rename fails loudly (T08-05).
- **MC-06.** `Scope` is exported from `lauren_di` with the reference's numeric values unchanged —
  `TRANSIENT = 0`, `REQUEST = 1`, `SINGLETON = 2`. The numbers are load-bearing because callers
  persist and compare scopes outside the library, so they are asserted, not merely documented
  (T08-06).
- **MC-07.** `injectable`, `post_construct` and `pre_destruct` keep their names and signatures and
  are exported from `lauren_di` itself; no caller needs a private submodule to reach them
  (T08-07).

The rename is one-way and final: the raw constant is **not exported** (PRD 01 §5.3.6), so no user can
write code that depends on the string. `injectable_meta()` is the supported reader, and a tool that
must inspect the attribute uses `vars(cls)` with the documented key recorded here.

### 5.3 Symbol-by-symbol mapping

#### 5.3.1 Imported names

| `lauren` (reference) | `lauren_di` | Notes |
|----------------------|-------------|-------|
| `from lauren import DIContainer` | `from lauren_di import DIContainer` | identical signature (PRD 01 §5.3) |
| `from lauren import injectable` | `from lauren_di import injectable` | identical signature: `(*args, scope=Scope.SINGLETON, provides=None, multi=False)` |
| `from lauren import post_construct, pre_destruct` | same names from `lauren_di` | marker values renamed (MC-02/03) |
| `from lauren import Scope` (re-export of `lauren.types`) | `from lauren_di import Scope` | `TRANSIENT=0`, `REQUEST=1`, `SINGLETON=2` unchanged |
| `from lauren import Token, Inject, OptionalDep` | same names from `lauren_di` | were in `lauren._di.custom`, now top level |
| `from lauren import use_value, use_class, use_factory, use_existing` | same names from `lauren_di` | recipes return `CustomProvider`; `inject=` keyword kept |
| `from lauren import Depends` (an `ExtractionMarker`) | `from lauren_di import Depends` | now a spelling alias: `Depends[T] is T` (D-25) |
| `from lauren._di import Provider, InjectableMeta` | `from lauren_di import Provider, InjectableMeta` | **promoted**: not bound on `lauren` (`E-402b`, `E-402c`) |
| `from lauren._di.custom import CustomProvider` | `from lauren_di import CustomProvider` | **promoted**: not bound on `lauren` (`E-402d`) |
| `from lauren._di import INJECTABLE_META` | — | **not exported**; use `injectable_meta()` (PRD 01 §5.3.6) |
| `from lauren._di._scope import register_framework_value, framework_value_types` | — | **deleted**; replaced by `value_tokens=` (MC-09) |
| `from lauren._lifecycle import LifecycleScheduler` | — | **deleted**; `startup()`/`shutdown()` (MC-16) |
| `from lauren._di import instance_lifecycle_hooks` | — | **deleted**; `Provider.*_chain` properties (MC-18) |

#### 5.3.2 `DIContainer` methods

The reference's method set is already almost the library's. The table lists the three differences and
confirms the rest by omission — a name absent from the "change" column is identical in name,
signature and meaning.

| Method | Change | Evidence |
|--------|--------|----------|
| `register(target, *, owning_module=None)` | none | `_di/__init__.py` |
| `register_value`, `register_instance`, `register_class`, `register_factory`, `register_alias`, `register_custom` | none — same names and keyword-only parameters | introspected signatures match PRD 01 §5.3 row for row |
| `compile()`, `has_provider()`, `get_provider()`, `all_providers()`, `registered_providers()`, `instance_index()`, `applied_overrides()`, `set_visible()` | none | PRDs 05–07 |
| `resolve(token, *, request_cache=None, framework_values=None, owning_module=None)` | **changed:** `framework_values` → `values` | D-52, MC-08 |
| `mark_singleton_initialized(cls)`, `set_singleton(cls, instance)`, `singletons` | **removed** from the public surface | D-56, MC-17 |
| `adopt_lifecycle(instance)` | **kept** — the embedder seam for a foreign object's lifecycle | D-56 |
| `startup()`, `shutdown()` | **added** — the scheduler's job, owned by the container | PRD 03 §5.6, MC-16 |
| `explain(token, *, owning_module=None)` | **added** — diagnostics (PRD 06 §5.8) | PRD 06 |
| `scope(*, values=None)` | **added** — `ResolutionScope`, the only place values are supplied | PRD 03, MC-11 |

#### 5.3.3 `Provider` fields

The reference's 18 fields are the library's 18 fields, with the same names (`E-402h` against PRD 02
§5.4). This is the single strongest compatibility result in the extraction and it deserves to be
stated as an invariant rather than a table: **no `Provider` field is renamed**. A test asserts the
set equality, so a future rename fails loudly (T08-05).

#### 5.3.4 Exceptions

| `lauren.exceptions` | `lauren_di` | Notes |
|---------------------|-------------|-------|
| `LaurenError` | `DIError` | one base for everything |
| `StartupError` | — | **collapsed**: there is no startup phase distinct from compile (D-14) |
| `LifecycleError` | `DIError` subclass, `LifecycleError` | kept as a grouping base |
| `CircularDependencyError`, `MissingProviderError`, `DIScopeViolationError`, `ProtocolAmbiguityError`, `UnresolvableParameterError`, `DuplicateBindingError`, `MetadataInheritanceError`, `DecoratorUsageError`, `LifecycleConfigError`, `LifecycleViolationError`, `DestructError`, `DestructTimeoutError`, `DrainTimeoutError` | same names | reparented under `DIError`; `detail` keys per PRD 05 |
| `HTTPError` and its 15 subclasses | — | **not exported** (MC-21) |
| `GuardConfigError`, `InterceptorConfigError`, `MiddlewareConfigError`, `ExceptionHandlerConfigError`, `RouterConflictError`, `RouteNotFoundError`, `CircularModuleError`, `ModuleExportViolation`, `ConfigValidationError`, `SessionConfigError`, `OpenAPISchemaError`, `MissingStateError`, `StateTypeError`, `ExtractorError`, `ExtractorFieldError` | — | framework subsystems, all **not supported** |
| — | `OverrideError` | new (PRD 07 row 33) |
| — | `InvalidProviderError` | new (D-16) |

- **MC-20.** Every exception the library raises is a subclass of `DIError`, and `DIError` is the only
  base a caller needs to catch. The reference's `LaurenError`/`StartupError` split has no analogue
  because there is no application-startup phase (T08-20).
- **MC-21.** No HTTP-bound exception is exported. A user catching `HTTPError` around `resolve()` was
  catching the wrong thing in the reference too: a DI failure there is a `StartupError` subclass, and
  the two are siblings, not parent/child (T08-21).
- **MC-22.** `OverrideError` and `InvalidProviderError` are additions with no migration path because
  there is no old name; the parity suite lists them in the divergence register (T08-22).

```python
# Migrating an except clause: the reference's hierarchy collapsed to one base.
from lauren_di import DIError, MissingProviderError

try:
    container.compile()
except MissingProviderError as exc:          # still the specific class
    ...
except DIError as exc:                        # replaces LaurenError AND StartupError
    ...
```

### 5.4 Replacement for `framework_values`: explicit value tokens

The reference's mechanism is a global registry plus a magic `Request` type:

```python
# lauren: the registry is process-global and never emptied
from lauren._di._scope import register_framework_value, framework_value_types

register_framework_value(JobContext)          # E-414: False -> True
assert JobContext in framework_value_types()
# ... and every later container in the process now skips JobContext (E-415)
# there is no unregister (E-416), and the registry is not per-container
```

The library replaces it with a declaration on the container and a value at the scope:

```python
from lauren_di import DIContainer, injectable


class Request:            # the host's own type; the library never names one
    ...


@injectable()
class Handler:
    def __init__(self, request: Request) -> None:   # a declared value token, not a provider
        self.request = request


container = DIContainer(value_tokens=(Request,))    # D-52: per container, immutable
container.register(Handler)
container.compile()                                 # Request is skipped because it is declared

request = Request()
async with container.scope(values={Request: request}) as scope:
    handler = await scope.resolve(Handler)
    assert handler.request is request

# A scope that forgets a declared token fails before constructing anything:
async def fail():
    async with container.scope() as scope:          # MissingProviderError, tokens=(Request,)
        ...
```

- **MC-08.** `resolve` takes `values=`, not `framework_values=`. The keyword is the only rename on the
  hot path; `request_cache` keeps its name because the cache genuinely is per-request and caller-owned
  (PRD 01 §5.3) (T08-08).
- **MC-09.** Value tokens are declared per container, with `DIContainer(value_tokens=(...))`. There is
  no module-level registry, no import-time registration and no way for one test to change another
  test's container. Two containers with different declarations disagree, in one process (`E-414`…
  `E-416` characterise what the reference cannot do) (T08-09).
- **MC-10.** `compile()` special-cases no type. `Request` is skipped only because a caller declared it
  a value token; remove the declaration and `compile()` raises `MissingProviderError` for `Request`
  exactly as for any other unregistered dependency (`E-411`) (T08-10).
- **MC-11.** A declared value token that a scope does not supply fails at **scope entry**, before any
  provider is constructed, with `MissingProviderError(detail={"tokens": (...), "hint": "pass values="})`.
  The reference instead fails mid-construction, after dependencies have been built (`E-412`) (T08-11).
  The documented cost: a declared-but-unused token is still required at every scope — declaring is a
  promise that the runtime always supplies it.

### 5.5 Replacement for `ExtractionMarker` awareness: a field predicate

The reference's DI core knows about HTTP markers by importing them lazily:

```python
# lauren, _di/__init__.py:2313
def _is_extractor_marker(obj):
    from ..extractors import ExtractionMarker      # inside the DI core
    return isinstance(obj, type) and issubclass(obj, ExtractionMarker)
```

That one call is why `Annotated[str, Path]` on a class field becomes a dependency on `str`, and why
the container then fails with a message about `str` rather than about a misplaced HTTP marker:

```
E-404 UsesPath    MissingProviderError: {'token': 'str'} | No provider for str
E-405 UsesHeader  MissingProviderError: {'token': 'str'} | No provider for str
```

The library removes the import and offers an optional, stdlib-only predicate:

```python
from typing import Annotated, Any
from lauren_di import DIContainer, injectable


def ignore_http_markers(name: str, annotation: Any) -> bool:
    """Return True when this class-body field must NOT be injected.

    The host supplies this; the library ships no default that knows about
    HTTP. Pure stdlib, no imports from the caller's framework required.
    """
    return getattr(annotation, "__metadata__", ()) and any(
        getattr(meta, "__name__", "") in {"Path", "Header", "Query", "Cookie"} - {""}
        for meta in annotation.__metadata__
    )


container = DIContainer(field_predicate=ignore_http_markers)


@injectable()
class Controller:
    q: Annotated[str, "Path"]      # the predicate rejects it: not a DI field
```

- **MC-12.** Class-body field collection consults `field_predicate(name, annotation)` when one is
  configured, and skips the field when it returns `True`. With no predicate, the collector applies
  PRD 02 §5.5's narrow rules only — `@injectable` class, runtime-checkable `Protocol`, `Inject`, and
  `Depends[T]`. Nothing else (`E-403`…`E-407`) (T08-12).
- **MC-13.** `Depends[T]` is `T`. It is not a marker instance, has no runtime semantics, and
  `param: Depends[Repo]` is indistinguishable from `param: Repo` (D-25). The reference's `Depends` is
  an `ExtractionMarker` subclass in `lauren.extractors` (`E-408`, `E-408b`, `E-408d`) (T08-13).
- **MC-14.** The eleven HTTP extractor markers are **not supported** as DI declarations. `Path`,
  `Header`, `Query`, `Cookie`, `Json`, `Form`, `State`, `Bytes`, `UploadFile`, `ByteStream`,
  `HostParam` name a parameter's *source in a request*; a DI-only library has no request. A field
  annotated with one is plain data, not a dependency — and the codemod flags it (§5.11) (T08-14).
- **MC-15.** The predicate must be stdlib-only and must not import the host framework. The library
  calls it during `compile()`; an exception raised by the predicate propagates as
  `DecoratorUsageError(detail={"field": name, "predicate": fn.__qualname__})`. This keeps the
  reference's one good instinct — `pydantic` must not be on the DI import path (T08-15).

### 5.6 Replacement for `LifecycleScheduler`: container-owned startup

The reference splits lifecycle across two objects. The container can construct and cache, but it does
not fire hooks; a separate `LifecycleScheduler` computes the topological order and invokes them:

```
E-420  resolve alone fires post_construct -> [] (nothing fired)
E-422  compute_order                      -> ['Service']
E-423  after run_post_construct           -> ['Service.up']
E-424  after run_pre_destruct             -> ['Service.up', 'Service.down']
E-426  container exposes: adopt_lifecycle, mark_singleton_initialized, set_singleton, instance_index
```

```python
# lauren_di: one owner, two methods, no scheduler
container = DIContainer()
container.register(Service)
container.compile()
await container.startup()      # every singleton, dependency order first (PRD 03 §5.6)
service = await container.resolve(Service)   # already initialised
await container.shutdown()     # reverse creation order, DestructError aggregation
```

- **MC-16.** `startup()` and `shutdown()` replace `compute_order` + `run_post_construct` +
  `run_pre_destruct`. `startup()` is idempotent and restartable after `shutdown()` (PRD 03 §5.10,
  D-22) (T08-16).
- **MC-17.** No public member exists solely for an external lifecycle driver. `set_singleton`,
  `mark_singleton_initialized` and `singletons` are gone. `adopt_lifecycle()` stays because it is the
  embedder's seam for an object the container did not construct, and `instance_index()` stays because
  it answers a diagnostic question ("which provider built this instance?") without the internals
  (`E-426`) (T08-17).
- **MC-18.** `instance_lifecycle_hooks()` is not carried. It is also broken in the reference: the
  documented instance form raises, because it reaches for `cls.__mro__` on the object (`E-421`); the
  class form works (`E-421b`). The library exposes the same information as
  `Provider.post_construct_chain` / `pre_destruct_chain`, which take no argument at all (T08-18).
- **MC-19.** Hook timing follows PRD 03's split, which differs from the reference's all-at-startup
  behaviour: a `REQUEST`-scoped instance's `@post_construct` fires at resolve; a `SINGLETON`'s fires
  at `startup()`, not at first resolve (`E-420` shows the reference firing neither at resolve) (T08-19).

### 5.7 Deprecation and alias strategy (resolves D-10)

D-10's three options are not equal. **Rename-only** is honest but hostile: a user with 200 decorated
classes has no mechanical path. **Re-export shims inside the library** invert the dependency and are
forbidden (MC-23). **A shim in the framework** costs the framework a small module and gives users a
dated window — and it keeps the source of truth in one place.

**Decision D-50: the library ships clean names with no aliases; the framework MAY ship a
`lauren.compat` module for exactly one minor release, which re-exports library names, translates the
`framework_values=` keyword with a `DeprecationWarning`, and is removed on a published date.**

```python
# lauren/compat.py  -- lives in the FRAMEWORK, never in lauren_di
import warnings

from lauren_di import (                      # re-export, do not re-implement
    DIContainer as _DIContainer,
    DIError,
    Provider,
    InjectableMeta,
    Scope,
)


class DIContainer(_DIContainer):
    """One-release shim: accepts the old keyword, warns, then delegates."""

    async def resolve(self, token, *, framework_values=None, **kwargs):
        if framework_values is not None:
            warnings.warn(
                "resolve(framework_values=...) is deprecated; use resolve(values=...)",
                DeprecationWarning,
                stacklevel=2,
            )
            kwargs["values"] = framework_values
        return await super().resolve(token, **kwargs)


__all__ = ["DIContainer", "DIError", "Provider", "InjectableMeta", "Scope"]
```

- **MC-23.** `lauren_di` has no runtime dependency on `lauren`: no import, no `try/except ImportError`,
  no `sys.modules` probing. The dependency arrow points framework → library, and a test asserts the
  library's source contains no `lauren` import (T08-23).
- **MC-24.** Any shim lives in the framework's own namespace (`lauren.compat`), is documented as
  temporary, and re-exports rather than re-implements. The library's `__all__` never contains a
  deprecated name (T08-24).
- **MC-25.** The one accepted keyword divergence — `framework_values=` — is translated by the shim
  with a `DeprecationWarning` and `stacklevel=2`, so the warning points at the caller's line. The
  library itself rejects the old keyword with `TypeError` (T08-25).
- **MC-26.** The alias table is closed: `DIContainer`, `DIError`, `Provider`, `InjectableMeta`,
  `Scope` and the four `use_*` recipes. A name not in that list has no alias, and the framework's
  shim must not invent one (T08-26).
- **MC-27.** Removal is scheduled, not hoped for: the shim exists for one minor release of the
  framework, is announced in its changelog with the removal version, and a test asserts that the
  shim's module docstring carries a `Removed in` line. After removal, `from lauren.compat import *`
  raises `ImportError` — the failure is loud (T08-27).

### 5.8 Parity test strategy

Parity cannot mean "the same test file imports both packages", because the two disagree on purpose in
a few named places. The harness therefore puts every divergence in an **adapter** and writes each
invariant once:

```python
# tests/parity/conftest.py  (sketch of the shipped harness)
import pytest


class Backend:
    name = "abstract"
    module = ""

    def load(self):
        try:
            self.mod = __import__(self.module, fromlist=["DIContainer"])
            return True
        except ImportError:
            return False

    # the ONLY places the two implementations differ
    def value_token(self): raise NotImplementedError
    def declare_value_type(self, token): raise NotImplementedError
    def container(self, *, value_tokens=()): raise NotImplementedError
    def resolve_values(self, mapping): raise NotImplementedError


class ReferenceBackend(Backend):
    name, module = "lauren", "lauren"

    def value_token(self):
        return type("RefValueToken", (), {})

    def declare_value_type(self, token):
        from lauren._di._scope import register_framework_value
        register_framework_value(token)

    def container(self, *, value_tokens=()):
        return self.mod.DIContainer()

    def resolve_values(self, mapping):
        return {"framework_values": mapping}


class LibraryBackend(Backend):
    name, module = "lauren_di", "lauren_di"

    def value_token(self):
        return type("LibValueToken", (), {})

    def declare_value_type(self, token):
        pass                       # declared per container, below

    def container(self, *, value_tokens=()):
        return self.mod.DIContainer(value_tokens=value_tokens)

    def resolve_values(self, mapping):
        return {"values": mapping}


@pytest.fixture(params=[ReferenceBackend(), LibraryBackend()], ids=lambda b: b.name)
def backend(request):
    if not request.param.load():
        pytest.skip(f"{request.param.module} is not installed")
    return request.param
```

- **MC-28.** One `backend` fixture, parameterised by adapter; invariant bodies touch only
  `backend.injectable()`, `backend.container()` and `backend.resolve_values()`. No invariant imports
  `lauren` or `lauren_di` directly (T08-28).
- **MC-29.** A backend that cannot be imported reports **SKIP**, never PASS. A skipped run is visibly
  incomplete in the summary, so "green" cannot mean "one side never ran" — the failure mode the
  prototype shows for `lauren_di` today (`E-430`…`E-433`) (T08-29).
- **MC-30.** Invariants assert **behaviour** — types, identities, order, exception class and `detail`
  keys — never object addresses, `repr` strings or `__all__` contents. Two implementations may hold
  different-looking objects that satisfy the same invariant (T08-30).
- **MC-31.** A divergence that is deliberate is recorded in §5.10's register and excluded from the
  invariant it would break. There is no "xfail" bucket: an unexpected divergence is a failure, not a
  tolerated difference (T08-31).
- **MC-32.** The harness records `lauren.__file__` and the sha256 of its `_di/__init__.py` at session
  start, and fails if `lauren.__file__` is outside the pinned checkout. This environment demonstrably
  has a *different* installed build on the default path (§1.5), so an unpinned parity run measures the
  wrong code (T08-32).
- **MC-33.** The fixture pattern is a fresh container per test, per backend: `container()` is called
  inside the test body, never shared, and no module-level `DIContainer` exists in the suite. This is
  PRD 07's CG-1 applied to the harness itself (T08-33).
- **MC-34.** Migration is complete when every row of §5.1 has a disposition and every invariant in
  §5.10 is PASS on the library and PASS-or-recorded-divergence on the reference. The check is
  mechanical: the register and the table are compared by a test (T08-34).

Run shape, against the reference today:

```
E-430 inv_constructor_injection    lauren=PASS | lauren_di=SKIP
E-431 inv_singleton_identity       lauren=PASS | lauren_di=SKIP
E-432 inv_value_supplied_token     lauren=PASS | lauren_di=SKIP
E-433 inv_missing_provider_detail  lauren=PASS | lauren_di=SKIP
```

### 5.9 Parity matrix

Each row is one invariant, the fixture that establishes it, and the outcome both sides must produce.
"Expected outcome" is stated as an observation, not an implementation.

| # | Invariant | Fixture | Expected outcome |
|---|-----------|---------|------------------|
| P-1 | Constructor injection resolves by annotation | `container()` + two `@injectable` classes | `isinstance(user.dep, Dep)` on both |
| P-2 | A singleton is cached per container | `container()`, two `resolve` calls | same object; a second container yields a different one |
| P-3 | A declared value token is supplied out of band | `backend.value_token()` + `container(value_tokens=…)` | resolved attribute **is** the sentinel object |
| P-4 | A missing provider names the token | `@injectable` depending on an unregistered class | `MissingProviderError` with `detail["token"]` on both; library raises at `compile()`, reference at `compile()` for this shape (`E-433`) |
| P-5 | Class-body field injection honours the annotation's token (`Depends[T]` is `T`, D-25) | `@injectable` with `x: Repo` (`Annotated[Repo, Inject(fn)]` for a function token) | field populated from the container |
| P-6 | An HTTP marker is not a DI declaration | field annotated `Annotated[str, Path]` | **divergence:** reference raises `MissingProviderError('str')` (`E-404`); library leaves the field alone |
| P-7 | Scope narrowing violation is rejected | singleton depending on a `REQUEST` provider | `DIScopeViolationError` with `dependent_scope`/`dependency_scope` |
| P-8 | `multi=True` collects into `list[T]` | two multi registrations of the same token | `resolve(list[T])` returns both, registration order |
| P-9 | Alias chains resolve and cycles fail | `use_existing` chain, then a self-alias | value forwarded; cycle → `CircularDependencyError` |
| P-10 | Generator providers tear down in reverse order | two generator providers with an append list | teardown order is `[b, a]`; a raised `DestructError` does not stop the second |
| P-11 | Singleton hooks fire at startup, request hooks at resolve | one `SINGLETON` + one `REQUEST` class with `@post_construct` | library: startup then resolve; **divergence:** reference fires neither at resolve and needs a scheduler (`E-420`, `E-423`) |
| P-12 | One build's overrides never leak into another | two containers, one `OverridePlan` | different values per container; no order dependence (PRD 07 CG-1) |

Rows P-6, P-11 and the `values=` keyword are the **complete** divergence register: three entries,
each already a decision in an earlier PRD (D-25, D-53, D-52). Every other invariant must hold on both
sides with no adapter help beyond construction.

### 5.10 Migration codemod

The mechanical part is small enough to specify exactly. `python -m lauren_di.migrate PATH` rewrites:

| From | To | Safety |
|------|----|--------|
| `from lauren import X` where `X` ∈ the §5.3.1 table | `from lauren_di import X` | mechanical |
| `from lauren._di import X`, `from lauren._di.custom import X` | `from lauren_di import X` | mechanical |
| `from lauren.types import Scope` | `from lauren_di import Scope` | mechanical |
| `resolve(..., framework_values=V)` | `resolve(..., values=V)` | mechanical |
| `"__lauren_injectable__"` and the two hook strings, if literal | the `_di` spellings | mechanical, and flagged |
| `Annotated[X, Path]` / `Header` / `Query` / … on a **class-body field** | unchanged, **reported** | manual: needs a decision (drop, or move to a handler parameter) |
| `LifecycleScheduler(...)` / `run_post_construct` | `container.startup()` | reported, not rewritten: the call sites differ |
| `register_framework_value(X)` | `DIContainer(value_tokens=(…, X))` | reported: the declaration moves to the container |

- The codemod never edits a file it cannot parse, never touches a string that is not an exact marker,
  and prints a unified diff by default (`--write` to apply). It has no network access and no
  dependency on `lauren`.
- The last three rows are **reports**, not rewrites, because each needs a human decision. A codemod
  that silently deleted a `Path` annotation would change behaviour without saying so.

---

## 6. Architecture Fit & Feasibility

### 6.1 Layering

```
lauren_di            (this library)   -- stdlib only, no framework names
   ^
   |  imports (one way, optional)
lauren               (framework)      -- may ship lauren.compat for one release
   |
   +-- lauren.extractors   (HTTP parameter sources: Path, Header, ...)  -> NOT DI
   +-- lauren._lifecycle   (LifecycleScheduler)                          -> absorbed
   +-- lauren._scheduling, lauren._commands (register_framework_value)   -> per-container
```

The arrow is the whole architecture: the library is a leaf. Every coupling in §5.1 is a case of the
reference pointing the wrong way, and each replacement removes one arrow.

### 6.2 What stays identical, and the two amendments

Identical (and tested as such): `@injectable()`'s signature and contract, `InjectableMeta`'s three
fields, all 18 `Provider` fields, all six `register_*` methods, `compile`/`resolve`'s async contract,
`Scope`'s numeric values, `Token`/`Inject`/`OptionalDep`/`CustomProvider`/`use_*` semantics, and the
DI-relevant exception names.

Two amendments to earlier PRDs, both recorded in their owner rather than asserted here:

1. **PRD 01's `DIContainer` constructor gains `value_tokens=`** (D-52). PRD 01 §5.3's row is updated
   to `DIContainer(*, overrides=None, strict_request_scope=False, value_tokens: Iterable[Any] = ())`,
   and its `resolve` row already says `values=`.
2. **PRD 02 §5.5 gains the `field_predicate` hook** (D-54), which narrows what `_collect_field_deps`
   collects. The 18 `Provider` fields are untouched — the predicate runs at collection time, before a
   `Provider` exists, which is why no field is needed to record it.

If either amendment is rejected at review, the fallback is stated: `value_tokens` becomes
`declare_value_token()`, and `field_predicate` becomes a module-level registration on the container's
`register(...)` call. Neither fallback changes §5.1's dispositions.

### 6.3 Risks

| # | Risk | Likelihood | Mitigation |
|---|------|-----------|------------|
| 1 | A user's class annotated with `Path` silently stops being injected | High (it is the one behaviour change) | codemod reports it; MC-14 names it; a docs "migrating" page leads with it |
| 2 | The installed-vs-checkout confusion makes parity evidence meaningless | **Observed** (§1.5) | MC-32 pins `lauren.__file__` and records a checksum |
| 3 | `lauren.compat` drifts into a permanent dependency | Medium | D-50 dates its removal; MC-27 makes the removal testable |
| 4 | The predicate becomes a back door for framework awareness | Medium | MC-15: stdlib-only, called only at compile, exceptions normalised |
| 5 | Parity suite degrades to "both skipped" | Medium | MC-29: SKIP is never PASS; CI requires at least one non-skip backend |
| 6 | `instance_lifecycle_hooks` removal breaks a user who passed a class | Low | it was never exported from `lauren`; MC-18 names the replacement |

### 6.4 Cost

| Operation | Cost | Bound |
|-----------|------|-------|
| `DIContainer(value_tokens=…)` | one `frozenset` | `O(#value tokens)` |
| `compile()` value-token skip | one set-membership per dependency | `O(#deps)` |
| Scope entry with `values=` | one dict copy + one set difference | `O(#value tokens)` |
| Field collection with a predicate | one call per class-body annotation | `O(#fields)` |
| Marker rename | import-time, one `setattr` per decorated target | `O(1)` per target |
| Parity suite | one container per test per backend | PRD 07 CG-1 |

No cost is added to `resolve` on the common path: a container with no value tokens and no predicate
performs one `frozenset` emptiness check.

### 6.5 Evidence index

All output reproduced with `PYTHONPATH=/root/python_projects/lauren-all/lauren-framework` and the
resolved module asserted to be the checkout.

| Script | Ids | What they gate |
|--------|-----|----------------|
| `p1.py` | `E-401` … `E-402i` | marker names and values, the export gap, `Provider`/`InjectableMeta` field sets |
| `p2.py` | `E-403` … `E-408e` | which annotations become DI fields; `Path`/`Header` failure mode; `Depends` identity |
| `p3.py` | `E-411` … `E-416b` | `framework_values` at compile and resolve; the global registry's cross-container reach |
| `p4.py` | `E-420` … `E-426` | hooks fire only under the scheduler; `instance_lifecycle_hooks` defect; scheduler plumbing |
| `p5.py` | `E-430` … `E-436` | the adapter harness runs the reference green and skips the absent library |

---

## 7. Error Catalog Impact

No new exception class is introduced by this PRD. Three catalogue rows are amended and one is
confirmed.

| Row | Change |
|-----|--------|
| `DecoratorUsageError` (PRD 05 catalogue) | gains one trigger: a `field_predicate` that raises is normalised to `DecoratorUsageError(detail={"field", "predicate"})` (MC-15) |
| `MissingProviderError` | gains the value-token trigger with `detail={"tokens", "hint"}` at scope entry (MC-11); unchanged for ordinary dependencies |
| `OverrideError` (row 33) / `InvalidProviderError` (D-16) | confirmed as library-only; §5.3.4 lists them as having no reference counterpart (MC-22) |
| `LaurenError`, `StartupError` | **not** in the catalogue: collapsed into `DIError` (MC-20) |

The 16 `HTTPError` subclasses and the 15 framework-subsystem exceptions are explicitly **outside** the
catalogue (MC-21), which is what PRD 05 §7's "framework-only" column already asserts.

---

## 8. Phased Implementation Plan

| Phase | Version | Work | Exit criterion |
|-------|---------|------|----------------|
| 0 — Pin | pre-0.1 | evidence runner asserts `lauren.__file__` is the checkout and records the `_di` sha256 | T08-32 green before any parity claim |
| 1 — Names | `0.1.0` | marker renames, `lauren_di` package layout, top-level exports of `Provider`/`InjectableMeta`/`CustomProvider` | T08-01…T08-07 green |
| 2 — Values | `0.1.0` | `values=` on `resolve`, `value_tokens=` on the container, scope-entry validation, delete the global registry | T08-08…T08-11 green |
| 3 — Predicate | `0.2.0` | `field_predicate`, `Depends[T] is T`, refuse extractor markers | T08-12…T08-15 green |
| 4 — Lifecycle | `0.2.0` | `startup()`/`shutdown()`, internalise scheduler plumbing, delete `instance_lifecycle_hooks` | T08-16…T08-19 green |
| 5 — Errors | `0.2.0` | single `DIError` base, drop the HTTP half | T08-20…T08-22 green |
| 6 — Parity | `0.3.0` | adapter harness, P-1…P-12, divergence register enforced | T08-28…T08-34 green |
| 7 — Migration | `0.3.0` | codemod, `lauren.compat` contract published, docs page | T08-23…T08-27 green |

Phase 0 is first on purpose: every later phase's evidence depends on measuring the intended artifact.

---

## 9. Documentation & Convention Updates

1. **`docs/migrating-from-lauren.md`** — new. The §5.3 tables verbatim, the §5.10 codemod, and a
   "what changes behaviour" section led by the `Path`-on-a-field case (risk 1).
2. **`docs/seams.md`** — new. The three replacements (§5.4–§5.6) stated as "the framework used to
   give you X; here is the generic Y and why".
3. **`docs/parity.md`** — new. The adapter contract, the SKIP rule, the divergence register, and how
   to run the suite against a pinned checkout.
4. **`README.md`** — a "Relationship to `lauren`" paragraph: extraction, one-way dependency, no
   runtime coupling.
5. **`llms.txt` / `llms-full.txt`** — every name in §5.3.1 and every container method in §5.3.2 must
   appear, so the docs-contract check (goal #11) covers the migration surface.
6. **`CHANGELOG.md`** — one "Changed" entry per phase in §8; the marker rename gets its own bullet
   because it is the only change that touches user data.
7. **`lauren_di.migrate`'s `--help`** — the §5.10 table, so the tool documents itself.

Convention: no document may call `lauren` a dependency of `lauren_di`; the phrasing is "extracted
from" and "consumed by".

---

## 10. Test Plan

### 10.1 Enumerated test cases

| Test | Rule | Asserts |
|------|------|---------|
| T08-01 | MC-01 | `injectable()` sets exactly `__lauren_di_injectable__`; no other `__lauren*` key |
| T08-02 | MC-02 | `post_construct()` sets `__lauren_di_post_construct__` |
| T08-03 | MC-03 | `pre_destruct()` sets `__lauren_di_pre_destruct__` |
| T08-04 | MC-04 | `InjectableMeta` has exactly `scope`, `provides`, `multi` |
| T08-05 | MC-05 | no public `lauren_di._di` path; every §5.3.1 name imports from the package root; `Provider`'s field set equals the reference's 18 names, exactly |
| T08-06 | MC-06 | `Scope.TRANSIENT/REQUEST/SINGLETON` are `0/1/2` and importable from `lauren_di` |
| T08-07 | MC-07 | `injectable`, `post_construct`, `pre_destruct` import from `lauren_di` and keep their reference signatures |
| T08-08 | MC-08 | `resolve(values=…)` supplies a value; `resolve(framework_values=…)` raises `TypeError` |
| T08-09 | MC-09 | two containers with different `value_tokens` disagree in one process; no global registry exists |
| T08-10 | MC-10 | `Request` is skipped only when declared; undeclared → `MissingProviderError` at `compile()` |
| T08-11 | MC-11 | a scope missing a declared token raises before any constructor runs (constructor counter == 0) |
| T08-12 | MC-12 | a predicate returning `True` removes the field; `False`/absent keeps PRD 02's rules |
| T08-13 | MC-13 | `Depends[Repo] is Repo`; `Depends` has no runtime marker |
| T08-14 | MC-14 | `Annotated[str, Path]` on a class field is not a dependency and does not raise |
| T08-15 | MC-15 | a raising predicate becomes `DecoratorUsageError` with `field` and `predicate` |
| T08-16 | MC-16 | `startup()` fires singletons in dependency order; twice is idempotent; `shutdown()` then `startup()` works |
| T08-17 | MC-17 | `mark_singleton_initialized`, `set_singleton`, `singletons` are absent; `adopt_lifecycle()` and `instance_index()` remain |
| T08-18 | MC-18 | `Provider.post_construct_chain` / `pre_destruct_chain` report the same tuples as `E-421b` |
| T08-19 | MC-19 | `REQUEST` hook at resolve, `SINGLETON` hook at `startup()`, neither at the other point |
| T08-20 | MC-20 | every raised DI exception is a `DIError`; no `StartupError` exists |
| T08-21 | MC-21 | `HTTPError` and the framework-subsystem names are absent from `lauren_di.__all__` |
| T08-22 | MC-22 | `OverrideError` and `InvalidProviderError` exist and subclass `DIError`; the divergence register lists them |
| T08-23 | MC-23 | `lauren` appears in no `import` statement in the library source (AST scan) |
| T08-24 | MC-24 | no deprecated name in `lauren_di.__all__`; the shim, if present, lives in `lauren.compat` |
| T08-25 | MC-25 | the shim warns with `DeprecationWarning` and `stacklevel=2` at the caller's line |
| T08-26 | MC-26 | the alias table has exactly the six §5.7 names plus the four `use_*` recipes |
| T08-27 | MC-27 | the shim's docstring carries a `Removed in` version and a test reads it |
| T08-28 | MC-28 | no parity invariant imports `lauren` or `lauren_di` directly |
| T08-29 | MC-29 | an unimportable backend yields SKIP for every invariant and zero PASS |
| T08-30 | MC-30 | no invariant asserts `repr`, `id`, or `__all__` |
| T08-31 | MC-31 | an unexpected divergence fails the run; the register has exactly three entries |
| T08-32 | MC-32 | the session fails when `lauren.__file__` is outside the pinned checkout |
| T08-33 | MC-33 | each test gets a fresh container per backend; no module-level container in the suite |
| T08-34 | MC-34 | every §5.1 row has a disposition and every §5.9 invariant has a recorded outcome |

### 10.2 Parity suite

The P-table in §5.9 is the suite. Twelve invariants, one parametrised fixture, three recorded
divergences. It runs under the `integration` marker against a pinned checkout, and under the unit
marker against the library alone (where the reference backend SKIPs).

### 10.3 Acceptance traceability

| Requirement | Where |
|-------------|-------|
| Symbol-by-symbol `lauren` → `lauren_di` mapping | §5.3.1–§5.3.4 |
| `__lauren_*__` marker renames | §5.2, D-51, MC-01…MC-03 |
| `framework_values` → scope values | §5.4, D-52, MC-08…MC-11 |
| `ExtractionMarker` awareness → pluggable field predicate | §5.5, D-54, MC-12…MC-15 |
| `LifecycleScheduler` → container-owned startup | §5.6, D-53, MC-16…MC-19 |
| Behavioural-parity strategy runnable against both | §5.8 (harness), §5.9 (matrix), `E-430`…`E-433` |
| Deprecation/alias strategy | §5.7, D-50, MC-23…MC-27 |
| Every coupling has a replacement or a "not supported" | §5.1, C-1…C-24 |
| Parity matrix names invariant, fixture, expected outcome | §5.9, P-1…P-12 |
