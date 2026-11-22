# PRD 02 — Core Container & Provider Registration

**Status:** Draft
**Date:** 2026-11-21
**Author:** DI extraction study (2026-11-21)
**Scope:** `prds/02-core-container-and-provider-registration.md` — what `@injectable()` promises, what
`DIContainer.register()` does with its output, every field of the `Provider` record, constructor and
class-body field injection (including the field collector's exact admission rules), function and
generator providers, annotation/forward-reference introspection, and the zero-configuration path from
"three calls" to a resolved object. Container lifetimes, teardown and scope validation are PRD 03;
tokens, aliases, `use_*` and `multi`/`list[T]` are PRD 04; `compile()` phases and the error catalogue
are PRD 05.
**Type:** Behavioural specification + parity record
**Priority:** P0 (PRD 03–07 all build on the registration rules fixed here)
**Target release:** `0.1.0` (phase 1 of PRD 01 §8)
**Depends on / related:** PRD 01 (public names, `Provider` field set, `Scope`, D-1…D-4, D-13, D-14);
consumed by PRDs 03–08. Decisions added here: D-15, D-16, D-17 (`prds/README.md` §4).
**Reference:** `../lauren-framework/lauren/_di/__init__.py` (2,478 lines) and
`../lauren-framework/lauren/decorators.py` (`injectable`), `../lauren-framework/lauren/_metadata.py`
(`reject_bare_usage`). Every rule in §6.2 was executed against that implementation on 2026-11-21; the
observed behaviour column quotes the run, not the docstrings.
**Inspired by:** NestJS providers (decorator + container), FastAPI `Depends` (generator dependencies,
annotation-driven injection), `injector`/`punq` (minimal registration), `dataclasses` and Pydantic v2
(`__signature__`-published constructor surfaces), and .NET's "the container constructs, it does not
configure".

---

## 1. Problem Statement

Registration is where a DI library either stays honest or becomes magic. The reference implementation
gets the hard parts right, but its registration contract is documented only in docstrings and comments
— and in at least three places the code and the prose disagree. Extracting it into a library means
writing the contract down before it is copied.

### 1.1 The decorator's contract is implicit

`injectable(*args, scope, provides, multi)` (`lauren/decorators.py:88`) accepts two target shapes
(class, function) and three parameters, but nothing states:

- that the bare `@injectable` form is rejected (the behaviour lives in `_reject_bare_usage` in
  `lauren/_metadata.py:61`, reached from `injectable` line 121);
- that the decorator *returns the target unchanged* and only attaches a marker attribute
  (`setattr(target, INJECTABLE_META, meta)`), so it neither wraps nor registers anything;
- that the marker is **not** inherited and reading it off a subclass raises
  `MetadataInheritanceError` (`_get_injectable_meta`, line 1585);
- what `provides` and `multi` actually bind (they are consumed later, by `_bind_token`, line 438).

A library cannot ship "decorator semantics" as tribal knowledge.

### 1.2 The `Provider` record is the real public API, and it is undocumented

`Provider` (line 187) carries **18 fields**, several of which are load-bearing for later PRDs:
`factory` vs `cls`, `is_function_provider`, `is_generator_provider`, `field_deps`,
`optional_dep_names`, `positional_arg_names`, the two hook tuples, `alias_target`, `provider_kind`.
PRD 01 §5.3.2 lists the field names; nothing yet says what each one means, who sets it, or which ones
a consumer may rely on. Downstream PRDs (03, 04, 07) all read this record.

### 1.3 Class-body field injection is a compatibility minefield

`_collect_field_deps` (line 2077) implements a deliberately narrow rule so that `@dataclass`, attrs and
plain classes keep working: a class-body annotation is a DI field only when it has no in-body value, is
not a dataclass field, resolves to a non-string, and passes `_looks_injectable` (line 2240). The rule
is subtle enough that guessing it wrong produces either "why is my `str` being resolved?" or "why is my
field `None`?". Worse, the field **timing** contradicts the `Provider` docstring: the docstring says
field deps are "set on the instance before `__init__` runs"; `_construct_class` (line 2369) actually
sets them **after** construction, via `object.__setattr__`, and swallows assignment failures. Both
statements cannot be true, and a user's `__init__` behaves differently depending on which one they
believed.

### 1.4 Introspection is where parity is won or lost

Constructor deps come from `inspect.signature(cls)` (not `cls.__init__`), so `__signature__`,
metaclass `__call__` and `__new__` all work (`_inspect_class_deps`, line 1813). Forward references are
resolved by three different helpers with different namespace strategies (`_resolve_class_signature_hints`
line 1869, `_safe_type_hints` line 1708, `_safe_class_hints` line 1751), including a stack-walking
fallback and a nested-quote peeling loop. `Annotated[T, Inject(x)]` is honoured in both constructor and
field position (`_extract_inject_token` line 2058), while any *other* `Annotated[T, …]` is unwrapped to
`T` (`_unwrap_annotated` line 2165). An extraction that "simplifies" any of this silently changes
resolution tokens — the failure mode is a `MissingProviderError` naming a type the user did register.

### 1.5 Two silent-acceptance defects

Verification against the reference found two cases where registration accepts input it can never
resolve:

| Input | Reference behaviour (observed) | Consequence |
|-------|-------------------------------|-------------|
| `container.register(SomeDecoratedInstance)` | Installs a *function* provider whose token is the instance and whose `factory` is the instance → `resolve(SomeClass)` still raises `MissingProviderError`, and the provider can never be called ("`Dep` object is not callable") | A typo (`register(Dep())` instead of `register(Dep)`) produces a container that looks configured and resolves nothing |
| `container.compile(); container.register(Late)` | `compile()` is idempotent (`if self._compiled: return`, line 980), so the late provider is never validated — a cycle or missing dep added after compile stays hidden | Validation silently stops meaning "the graph is valid" |

Both are cheap to reject. §5.3 and §6.4 (D-15, D-16) specify the corrections; PRD 08 records them as
the only intentional divergences in registration.

### 1.6 The zero-configuration path is never stated

The library's selling point is "decorate, register, resolve". Nothing currently says that the default
`@injectable()` is a singleton, that `register` accepts targets in any order, that dependencies need
not be registered first, or that the first `resolve` validates the graph (D-14). Users discover this
by reading `_di/__init__.py`, which is exactly what a library must not require.

---

## 2. Goals

| # | Goal | Acceptance signal |
|---|------|-------------------|
| G1 | One decorator, two shapes, no surprises | `@injectable()` works on classes and plain functions; the decorated object is returned unchanged (`is` identical); the bare form raises `DecoratorUsageError` |
| G2 | Marker semantics are exact | `injectable_meta()` returns the `InjectableMeta` or `None`; inherited metadata raises `MetadataInheritanceError` |
| G3 | `register()` is total and predictable | Every accepted target produces exactly one `Provider`; every accepted-then-unusable input is rejected with a named exception (§6.3) |
| G4 | The `Provider` record is fully documented | All 18 fields and both chain properties have a meaning, a producer and a consumer named in §5.4 |
| G5 | Constructor injection covers Python's real call protocol | Tests prove `__init__`, `__new__`, `__signature__`, keyword-only and positional-only parameters all yield identical `deps` |
| G6 | Field injection is narrow and explainable | The five field gates in §5.6 reproduce the reference's admissions *and* rejections, including `@dataclass` and primitive fields |
| G7 | Function and generator providers are first-class | A decorated function is its own token; generator setup runs at first resolve and teardown is owned by the container (PRD 03) |
| G8 | Introspection survives real-world annotations | PEP 563 strings, function-local types, nested quoting, `Annotated[T, Inject(x)]`, `Depends[T]` and unresolvable strings behave as §5.9 states |
| G9 | Zero-config is a documented path | The §5.11 program runs with three calls, no `compile()`, no scope handle and no particular registration order |
| G10 | Parity is checkable, not asserted | §6.2 maps 35 rules to a reference function + observed behaviour + an enumerated test case; §6.3 does the same for every rejection |

## 3. Non-Goals

PRD 02 specifies **registration and construction only**. The following are out of scope here and are
owned by the PRD named:

- **Lifetimes, caching, teardown, `Scope` narrowing.** `Scope` is read from the marker and stored on
  the record; how a lifetime is honoured is PRD 03. `@post_construct` / `@pre_destruct` *collection*
  is PRD 03 (the record's hook fields are only named here).
- **Strict vs lax request-scope resolution, scope context manager, `values`.** PRD 03.
- **`Token`, `Inject` semantics beyond its annotation role, `OptionalDep`, `CustomProvider`, the four
  `use_*` recipes, aliases, `multi=True`, `list[T]` collections.** Registration of those records is
  PRD 04; PRD 02 only says how `provides`/`multi` land on the `Provider`.
- **`compile()` phases, cycle detection, ambiguity detection, the error catalogue.** PRD 05. PRD 02
  fixes *when* validation is triggered (§5.11, D-14, D-15), not what it checks.
- **Module visibility (`owning_module`, `set_visible`).** PRD 06. `register` accepts and records the
  argument; it does not filter.
- **Overrides.** PRD 07. `register` hands off to the override path when a plan matches (D-12).
- **Extractor markers (`Path`, `Query`, …).** Non-goals of the product (PRD 01 §3). The reference's
  `_is_extractor_marker` (line 2313) is dropped; the field predicate seam is D-9 and PRD 08.
- **Automatic discovery, scanning or import-time registration.** Registration is always explicit
  (PRD 01 §3).
- **Dataclass/attrs/Pydantic integration beyond signature compatibility.** The collector must not
  mistake their fields for DI fields (G6); it must not add features for them.
- **Configuration objects, factory DSLs, builder chains.** `register` is a method, not a DSL.

## 4. Prior Art

| System | Registration model | What we keep | What we reject |
|--------|--------------------|--------------|----------------|
| NestJS | `@Injectable()` + module `providers` array; `useValue`/`useClass`/`useFactory`/`useExisting` | Decorator marker + explicit registration; `provides` as the binding list | Module arrays as the registration unit (PRD 06 decides visibility; there is no module compiler) |
| FastAPI | `Depends(callable)` at the parameter site; generator dependencies for setup/teardown | Generator-provider shape: code before `yield` is setup, after is teardown | Request-time resolution of HTTP parameters (not a library concern) |
| `injector` | `Module.configure(binder)` with `bind(Interface).to(Impl)` | Interface→implementation binding via `provides` | Binder DSL; explicit `register_*` methods read better and type-check |
| `punq` | `register(service, factory)`; no scopes | Minimal, callable-based registration | Scope-less-ness (we keep lifetimes) |
| `dependency-injector` | Provider objects composed in a container class | Providers as *data* (records), inspectable in tests | Container-as-configuration-object; mutable global state |
| Spring / .NET DI | Constructor injection as the default; class-body injection does not exist | Constructor-first; annotation-driven resolution | Field injection as a primary mechanism — ours is a narrow, opt-in extra for plain classes |
| Pydantic v2 / attrs | Publish the constructor surface via `__signature__` | `inspect.signature(cls)` as the single source of truth | Owning model construction or validation |

The common lesson: **registration is data, not control flow.** Every accepted target must lower into a
record a test can read back, and every rejected target must raise a named exception.

## 5. Proposed API

### 5.1 `@injectable()` — one decorator, two shapes

```python fragment
from lauren_di import Scope, injectable

@injectable()                        # singleton class provider, no extra tokens
class Config:
    dsn = "sqlite:///app.db"

@injectable(scope=Scope.REQUEST)     # explicit lifetime (honoured by PRD 03)
class Session:
    def __init__(self, cfg: Config) -> None:      # constructor injection
        self.dsn = cfg.dsn

@injectable(provides=[Session])      # also bind the Session token (PRD 04)
class SqlSession(Session):
    ...

@injectable()                        # function provider: the function is the token
def make_clock() -> Clock:
    return Clock()
```

Contract:

| Property | Rule |
|----------|------|
| Target kinds | A class, or any callable that is not a class (function, `functools.partial`, callable object) |
| Return value | The target itself, unchanged (`injectable()(X) is X`). No wrapper, no copy, no registration |
| Side effect | Exactly one attribute set: the marker (name per D-11), holding an `InjectableMeta` |
| Must be called | `@injectable` without parentheses raises `DecoratorUsageError` at decoration time |
| Defaults | `scope=Scope.SINGLETON`, `provides=None` → `()`, `multi=False` |
| Does **not** | validate dependencies, construct instances, register into a container, scan imports, or enforce scope |
| Attach failure | If the target refuses attribute assignment (e.g. `int`), `DecoratorUsageError` is raised |

```python fragment
# Rejected: the bare form hides intent and would bind the class as a positional argument.
# @injectable
# class Repo: ...
# -> DecoratorUsageError: @injectable must be used with parentheses: write '@injectable()' ...
```

### 5.2 `InjectableMeta` and `injectable_meta()`

`InjectableMeta` is the decorator's payload: `scope: Scope`, `provides: tuple[type, ...]`,
`multi: bool`. It is a plain dataclass (three fields, defaults `SINGLETON`/`()`/`False`) and the
container normalises `provides` to a tuple at decoration time, so `provides=[A, B]` and
`provides=(A, B)` produce identical records.

`injectable_meta(target)` is the only supported reader of the marker:

```python fragment
from lauren_di import Scope, injectable, injectable_meta

@injectable(scope=Scope.REQUEST, provides=[Repo])
class SqlRepo:
    ...

meta = injectable_meta(SqlRepo)
assert meta is not None and meta.scope is Scope.REQUEST and meta.provides == (Repo,)
assert injectable_meta(str) is None          # undecorated class
assert injectable_meta(lambda: 1) is None    # undecorated callable
```

Inheritance is deliberately **not** transitive:

```python fragment
@injectable()
class BaseRepo:
    ...

class SqlRepo(BaseRepo):        # forgot to re-decorate
    ...

injectable_meta(SqlRepo)        # -> MetadataInheritanceError
# "SqlRepo inherited injectable metadata from BaseRepo; re-decorate explicitly."
```

Reading the marker through a subclass would silently register the subclass with the base's lifetime and
`provides` list — exactly the kind of "works in dev, wrong in prod" bug the error prevents. A subclass
that is re-decorated is a normal provider (its own marker wins).

### 5.3 `DIContainer.register()`

```
register(target: type | Callable[..., Any], *, owning_module: type | None = None) -> Provider
```

`register` is the only entry point for decorated targets. In order:

| Step | Action | Failure |
|------|--------|---------|
| 1 | If an `OverridePlan` matches `target`, install the override instead (PRD 07, D-12) | PRD 07 |
| 2 | Read `injectable_meta(target)` | `MetadataInheritanceError` (inherited marker) |
| 3 | Require a marker | `MissingProviderError("{name} is not marked @injectable")` |
| 4 | Reject anything that is neither a class nor callable | `InvalidProviderError` (D-16; reference has no check) |
| 5 | Reject a token that is already registered | `DuplicateBindingError` |
| 6 | Inspect the target: `deps` (constructor or function parameters), plus `field_deps` for classes, plus lifecycle hooks for classes (PRD 03) | `UnresolvableParameterError`, `LifecycleConfigError` |
| 7 | Build the immutable-by-convention `Provider`, index it by its token and by every `provides` token | — |
| 8 | Invalidate the compiled flag (D-15) and the instance index | — |

```python fragment
from lauren_di import DIContainer

container = DIContainer()
cfg = container.register(Config)        # -> Provider
assert cfg.cls is Config and cfg.scope is Scope.SINGLETON
assert container.get_provider(Config) is cfg
```

Rules:

- **One target, one token.** A class registers under the class; a function registers under the
  function object. `provides=[...]` adds bindings but never changes the primary token.
- **No implicit re-registration.** Registering the same target twice is an error, not a no-op; use
  `has_provider()` (PRD 05) when a registration may be repeated.
- **Order is free.** Dependencies may be registered before or after their consumers; `register` never
  looks a dependency up. Only `compile()` / `resolve()` do (D-14).
- **No validation beyond signature inspection.** A missing dependency, a cycle or a scope violation is
  not detected here; it surfaces at validation time (PRD 05).
- **`owning_module` is recorded, not enforced.** Visibility filtering is PRD 06 (D-5).

### 5.4 The `Provider` record

`Provider` is a plain dataclass with 18 fields. It is a **value record**: the container finalises every
field during registration, and consumers must treat all fields as read-only. Two derived properties
(`post_construct_chain`, `pre_destruct_chain`) are the supported way to read the hook tuples.

| Field | Type | Meaning | Set for | Consumer |
|-------|------|---------|---------|----------|
| `cls` | `type` | The **token** the provider is indexed by. Despite the name it may be a function (function providers), a string, or a `Token` (custom providers, PRD 04) | all | resolution, caching key |
| `scope` | `Scope` | Lifetime declared on the marker | all | PRD 03 |
| `provides` | `tuple[type, ...]` | Additional tokens bound to this same provider | all | PRD 04 |
| `multi` | `bool` | Whether this provider participates in a collection for its token | all | PRD 04 |
| `deps` | `tuple[tuple[str, Any], ...]` | `(parameter_name, token)` in signature order; constructor/`__new__` parameters for classes, parameters for functions | all | instantiation |
| `post_construct` | `Callable \| None` | First `@post_construct` hook (legacy single-hook view) | classes | PRD 03 |
| `pre_destruct` | `Callable \| None` | First `@pre_destruct` hook (legacy single-hook view) | classes | PRD 03 |
| `owning_module` | `type \| None` | Module that declared the provider; `None` = global/test context | all | PRD 06 |
| `factory` | `Callable \| None` | The callable actually invoked: the class itself, the decorated function, or the `use_class`/`use_factory` target | all | instantiation |
| `field_deps` | `tuple[tuple[str, type], ...]` | `(attribute_name, token)` for class-body fields, in annotation order | classes (empty for functions) | instantiation |
| `is_function_provider` | `bool` | `True` when the target was a non-class callable | all | instantiation |
| `is_generator_provider` | `bool` | `True` when the function is a generator/async generator (setup/teardown) | functions | PRD 03 |
| `provider_kind` | `str \| None` | `None` for decorated providers; `"value"`/`"class"`/`"factory"`/`"existing"` for custom providers | all | PRD 04 |
| `alias_target` | `Any` | For `use_existing`: the token to delegate to | custom providers | PRD 04 |
| `optional_dep_names` | `frozenset[str]` | Parameter names declared `OptionalDep(...)`; a missing provider lowers to `None` | `use_factory` | PRD 04 |
| `positional_arg_names` | `tuple[str, ...]` | Parameter names to pass positionally (factory recipes) | `use_factory` | PRD 04 |
| `post_construct_hooks` | `tuple[Callable, ...]` | Every `@post_construct` hook, run order: bases first, definition order within a class | classes | PRD 03 |
| `pre_destruct_hooks` | `tuple[Callable, ...]` | Every `@pre_destruct` hook, run order: subclasses first | classes | PRD 03 |

Derived properties:

| Property | Returns | Fallback |
|----------|---------|----------|
| `post_construct_chain` | `post_construct_hooks` | `(post_construct,)` when the tuple is empty (hand-built providers) |
| `pre_destruct_chain` | `pre_destruct_hooks` | `(pre_destruct,)` when the tuple is empty |

Invariants a test may assert:

1. `provider.cls is target` for classes and `provider.cls is fn` for functions.
2. `provider.factory is provider.cls` for decorated providers (custom providers may differ, PRD 04).
3. `[name for name, _ in provider.deps]` is exactly the injectable parameter names, in signature order.
4. `provider.deps` and `provider.field_deps` never contain a name twice, never contain `self`/`cls`,
   and never contain `*args`/`**kwargs`.
5. `is_function_provider` and `is_generator_provider` are both `False` for class providers.
6. `provider_kind is None` for every provider created by `register`.

### 5.5 Constructor-parameter injection

Dependencies come from `inspect.signature(cls)` — the *class* as a callable — not from
`cls.__init__` directly. That single choice is what makes `__signature__`-publishing models, metaclass
`__call__` factories and `__new__`-heavy classes work without special cases.

```python fragment
from typing import Annotated
from lauren_di import Inject, injectable

@injectable()
class ReportService:
    def __init__(
        self,
        repo: ReportRepo,                           # by type
        clock: Annotated[Clock, Inject("CLOCK")],   # by explicit token
        users: UserRepo,                            # `Depends[UserRepo]` is the same token (D-25)
        limit: int = 100,                           # annotated + default = optional dep
        verbose=False,                              # no annotation + default = ignored
        *args,
        **kwargs,
    ) -> None: ...
```

| Parameter shape | Collected as a dep? | Token | If no provider exists |
|-----------------|--------------------|-------|-----------------------|
| `self`, `cls` | No | — | — |
| `*args`, `**kwargs` | No | — | — |
| No annotation, no default | **No — registration fails** | — | `UnresolvableParameterError` (at `register`) |
| No annotation, with default | No | — | the default is used; the container never passes it |
| `x: T` | Yes | `T` | `MissingProviderError` at compile/resolve |
| `x: T = default` | Yes | `T` | the default is used (dependency is optional) |
| `x: Annotated[T, Inject(tok)]` | Yes | `tok` (any hashable: type, `str`, `Token`) | `MissingProviderError` |
| `x: Depends[T]` (a runtime spelling that *is* `x: T`; D-25) | Yes | `T` | `MissingProviderError` — write `x: T` in a typed codebase (PRD 01 §5.5) |
| `x: "SomeType"` (resolvable string) | Yes | the resolved type | as a normal dep |
| `x: "NotDefinedAnywhere"` (unresolvable) | Yes, as a **string token** | the raw string | `MissingProviderError: No provider for NotDefinedAnywhere` |
| `def __init__(self, x: T, /)` — positional-only, would be collected | **No — registration fails** (D-19) | — | `UnresolvableParameterError` |

```python fragment
# Rejected at registration: an unannotated parameter cannot be resolved by type.
@injectable()
class Broken:
    def __init__(self, repo): ...
# -> UnresolvableParameterError: Broken parameter 'repo' missing annotation

# Also rejected: the container injects by keyword, so a positional-only
# dependency could be collected but never actually passed.
@injectable()
class AlsoBroken:
    def __init__(self, repo: ReportRepo, /): ...
# -> UnresolvableParameterError: AlsoBroken parameter 'repo' is positional-only
```

Keyword-only parameters (`def __init__(self, *, d: Dep)`) are collected normally: the container always
passes injected dependencies **by keyword**, filtering them against the target's signature
(`_filter_kwargs_for_signature`), so a `**kwargs` parameter receives everything and a namespace clash
never raises `TypeError`.

Positional-only parameters are the exception, and the reason is mechanical: a value passed by keyword
cannot bind to a positional-only parameter. The reference collects such a dependency and then drops it
at call time — `TypeError: X.__init__() missing 1 required positional argument` when the parameter is
required, and the parameter's own default (silently ignoring the registered provider) when it has one.
Both arrive late and name the wrong problem, so `lauren-di` rejects the target at registration instead
(D-19). A positional-only parameter that is **not** collected — unannotated with a default, or `*args`
— is unaffected, because the container never passes it.

### 5.6 Class-body field injection

Field injection exists so a plain class can receive collaborators without writing `__init__`. It is
deliberately narrow: **most annotations are data, not dependencies.**

```python fragment
from typing import Annotated
from lauren_di import Inject, injectable

@injectable()
class AuditService:
    logger: Logger                     # injected: no default, injectable type
    clock: Annotated[Clock, Inject(make_clock)]   # injected: the factory's token (§5.7)
    name: str = "audit"                # NOT injected: class-body default
    count: int                         # NOT injected: primitive type
```

The `clock` line is the one that needs a note. `Depends[make_clock]` is the *runtime* spelling of
the same token — it evaluates to the function, so the field's token is the function — but a
function is not a type, so a field annotated with it fails gate 4/6 (the hint is not a type) and is
skipped **silently**, exactly like `count: int`. `Annotated[Clock, Inject(make_clock)]` is the
spelling that works in both worlds: gate 5 short-circuits the type gate, the container resolves the
function's token, and a checker sees `Clock`. PRD 01 §5.5 states the rule; `tests/unit/` pins the
gate, so a future edit cannot re-promise injection for a non-type annotation.

Gates, in order — all must pass:

| # | Gate | Rule | Not satisfied → |
|---|------|------|-----------------|
| 1 | Own annotations only | The name appears in the class's **own** annotations (`inspect.get_annotations(cls, eval_str=False)`); inherited annotations belong to their owner | not a field (a subclass does not inherit field deps) |
| 2 | No in-body value | `name not in cls.__dict__` — neither a literal (`x: int = 5`) nor a dataclass descriptor (`x: int = field(...)`) | not a field |
| 3 | Not a dataclass field | `name not in cls.__dataclass_fields__` — dataclass fields are populated by the generated `__init__` (or a factory), even when they have no default | not a field |
| 4 | Resolvable annotation | The hint resolves to something other than a `str` (`_safe_class_hints(..., include_extras=True)`) | **silently skipped** (documented non-error) |
| 5 | Explicit token wins | `Annotated[T, Inject(tok)]` short-circuits gate 6 and contributes `tok` | — |
| 6 | `_looks_injectable` | See the type table below | **silently skipped** |

`_looks_injectable` (reference line 2240) decides by shape:

| Annotation | Injectable? | Why |
|------------|-------------|-----|
| `Annotated[T, <metadata>]` | recurse into `T`; `True` when the metadata contains `Inject` (a `Depends[T]` is just `T`, D-25) | markers are the explicit opt-in |
| `list[T]` | recurse into `T` | the only collection shape the container materialises (PRD 04) |
| other parameterised generic (`dict[K, V]`, `tuple[X, ...]`, `Optional[X]`) | No | cannot name a single provider |
| `str`, `bytes`, `bytearray`, `int`, `float`, `complex`, `bool`, `None`, `list`, `tuple`, `set`, `frozenset`, `dict` | No | plain data; resolving them is always a bug |
| class decorated `@injectable` | Yes | explicit marker |
| runtime-checkable `Protocol` (`_is_runtime_protocol`) | Yes | may be bound via `provides` or matched structurally (PRD 04/06) |
| any other user-defined class | Yes | last resort: the field is collected so a missing provider surfaces as `MissingProviderError` at validation instead of the attribute silently staying unset |
| non-type (e.g. a string, an instance) | No | — |

Examples of what this buys, all executed against the reference:

| Class body | `field_deps` | Reason |
|------------|--------------|--------|
| `d: Dep` | `(("d", Dep),)` | gates 1–6 pass |
| `d: Dep = None` | `()` | gate 2 |
| `@dataclass` with `d: Dep` | `()` | gate 3 |
| `s: str` | `()` | gate 6 (primitive) |
| `tags: list[str]` | `()` | gate 6 (generic over a primitive) |
| `x: "Undefined"` | `()` | gate 4 (unresolved string) |
| `db: Annotated[str, Inject("DB")]` | `(("db", "DB"),)` | gate 5 short-circuits 6 |
| `s: Speaker` (runtime `Protocol`) | `(("s", Speaker),)` | gate 6 (protocol) |

A collected field whose provider is missing is a **hard** dependency: `compile()`/`resolve()` raise
`MissingProviderError`. Fields have no default-fallback concept because a class-body default means the
field was never collected (gate 2).

One optional gate runs before all six: when `DIContainer(field_predicate=…)` is configured, the
collector calls `field_predicate(name, annotation)` and skips the field on a truthy result (PRD 08
§5.5, D-54). With no predicate the table above is the whole rule. This is where a host framework
declares "this annotation is an HTTP parameter source, not a dependency" without the DI core importing
the framework's marker classes; a predicate that raises is normalised to
`DecoratorUsageError(detail={"field", "predicate"})`.

**Timing.** Field values are resolved before construction but set **after** the instance exists:

1. resolve every `field_deps` token,
2. call `cls(**filtered_kwargs)` (so `__init__` sees only its own parameters, never the fields),
3. assign each field with `object.__setattr__`.

Consequences, stated because they are observable:

- `hasattr(self, "d")` inside `__init__` is `False`. If `__init__` needs the value, take it as a
  constructor parameter — that is the portable contract.
- Assignment failures are swallowed: an instance with `__slots__ = ()` (or any object rejecting the
  attribute) keeps the field unset and raises nothing. Field injection is best-effort by design; it
  never turns a working constructor into a failing one.
- Because gate 3 excludes dataclass fields, frozen dataclasses are unaffected in practice.

The reference's `Provider` docstring claims the opposite order ("set on the instance before `__init__`
runs"). The code (`_construct_class`, line 2369) is authoritative; this PRD specifies the code's
behaviour and PRD 08 lists the docstring as a documentation defect to fix upstream.

### 5.7 Function providers

Any decorated non-class callable becomes a provider whose **token is the function object**:

```python fragment
from typing import Annotated
from lauren_di import Inject, injectable

@injectable()
def make_session(cfg: Config) -> Session:
    return Session(cfg.dsn)          # the return value IS the dependency

@injectable()
class UserRepo:
    session: Annotated[Session, Inject(make_session)]   # depend on the factory by reference
```

Rules:

- The function's **parameters** are its dependencies (same rules as §5.5, including `Inject` and
  defaults); its **return annotation** plays no runtime role — it is documentation and typing only.
- The function's own token is the function, so `resolve(make_session)` works. `Depends[make_session]`
  is the *runtime* spelling of that token — it evaluates to the function, so the container receives
  the function as the token — but a function is not a type, so a field annotated with it is skipped
  by the field gates (§5.6). A typed codebase writes `Annotated[Session, Inject(make_session)]`,
  which resolves the same token and keeps `Session` for checkers (PRD 01 §5.5); the spelling is
  used where it is *evaluated*, e.g. `assert Depends[make_session] is make_session`.
- The return value is the dependency. If it is awaitable (`async def`), it is awaited.
- `provides=[...]` binds extra tokens to the same provider (PRD 04).
- Duplicate registration of the same function raises `DuplicateBindingError`.
- Functions cannot carry lifecycle hooks; `post_construct`/`pre_destruct` stay `None` and the hook
  tuples stay empty.

### 5.8 Generator and async-generator providers

A generator function is a provider whose `yield` is the setup/teardown boundary:

```python fragment
from lauren_di import injectable

@injectable()
def connection(cfg: Config):
    conn = connect(cfg.dsn)          # setup: runs during the first resolve
    try:
        yield conn                   # the dependency value
    finally:
        conn.close()                 # teardown: runs when the owning lifetime ends

@injectable()
async def async_connection(cfg: Config):
    conn = await aconnect(cfg.dsn)
    try:
        yield conn
    finally:
        await aclose(conn)
```

Rules:

- Detection is `inspect.isgeneratorfunction` or `inspect.isasyncgenfunction`, and it applies to
  both registration spellings — an `@injectable()` generator provider and a generator passed as
  `use_factory`/`register_factory`'s `factory`; the record carries `is_generator_provider=True`
  either way, so the two spellings cannot disagree.
- Resolution advances the generator exactly one step (`next()` / `__anext__()`); the yielded value is
  the dependency.
- Teardown advances the generator one further step, which runs the code after `yield` (typically a
  `finally`). The container owns the call: scope exit for request/transient lifetimes, `shutdown()` for
  singletons (PRD 03, D-7).
- Teardown is idempotent: a second close is a no-op, and `StopIteration`/`StopAsyncIteration` from a
  finished generator is swallowed.
- **`yield` exactly once.** The wrapper advances once at setup and once at teardown; a second `yield`
  leaves the generator suspended forever and the code after it (including `finally`) never runs. This
  is a documented constraint, not an error the container can detect reliably.
- Generator providers cannot be `Scope.TRANSIENT`: a transient instance is never tracked, so its
  teardown could never run. Registration raises `LifecycleConfigError` (D-17; the reference raises its
  `StartupError` base with the same message).
- A generator that raises before the first `yield` fails the resolution with that exception; nothing is
  cached.

### 5.9 Annotation introspection

Resolution tokens come from annotations, so the introspection pipeline is part of the public contract.

```python fragment
from __future__ import annotations          # PEP 563: every annotation is a string
from typing import Annotated
from lauren_di import Inject, injectable

@injectable()
class Repo:
    def __init__(self, db: Annotated[Connection, Inject("DB")]) -> None:
        self.db = db                        # token is "DB", not Connection
```

Pipeline, per position:

| Position | Signature source | Hint resolution | Fallback |
|----------|------------------|-----------------|----------|
| Constructor / `__new__` / `__signature__` | `inspect.signature(cls)` | `_resolve_class_signature_hints`: fast path, then globals from `__init__`/`__new__`, then the calling stack's locals, then the raw string | unresolved annotation stays a `str` → the string becomes the token |
| Function provider | `inspect.signature(fn)` | `_safe_type_hints`: `resolve_type_hints` (in-tree, PRD 08), then a stack-locals retry, then no hints | same as above |
| Class body | `inspect.get_annotations(cls, eval_str=False)` | `_safe_class_hints(include_extras=True)`: `get_type_hints(cls)`, then module globals + stack locals, then raw `__annotations__` | unresolved field annotation is skipped (gate 4) |

Token rules:

1. `Annotated[T, Inject(x)]` → token `x`, whatever `x` is (a type, a `str`, a `Token`). `T` is kept for
   static checkers only.
2. More than one `Inject` marker on one annotation → the **first** wins, silently. Duplicate markers
   are a programming error, but rejecting them would break legitimate re-decoration patterns; the
   reference takes the first, and parity is the contract.
3. Any other `Annotated[T, ...]` (including `Depends[T]`) → token `T`. `Depends` is a pure alias (`Depends[T] is T`, D-25), so the token is simply the inner type.
4. Nested quoting is peeled: under PEP 563, `x: "Upstream"` arrives as the string `"'Upstream'"`; the
   resolver evaluates until it stops shrinking (`_resolve_string_annotation`).
5. Function-local classes resolve through the stack-locals fallback, so a provider declared inside a
   test function still works.
6. A string that cannot be resolved is **not** an error at registration: constructor position keeps it
   as a string token (a legitimate token kind), field position drops the field.

### 5.10 Instantiation order

For one provider, `resolve` performs these steps in order (lifetime caching and scope handling are PRD
03; aliases and collections are PRD 04):

| # | Step | Notes |
|---|------|-------|
| 1 | Return a supplied runtime value (`values` mapping) as-is, if present | D-13 rename of the reference's `framework_values` |
| 2 | Consult the lifetime's cache | PRD 03 |
| 3 | Resolve constructor/function parameters (`provider.deps`) into `kwargs` | a missing provider falls back to `OptionalDep` → `None`, then to the parameter's own default, then re-raises |
| 4 | For classes, resolve `provider.field_deps` into a field-value map | before construction, applied after it |
| 5 | Call `factory(**filtered_kwargs)`; await if awaitable | generators: advance to `yield` instead |
| 6 | Assign field values with `object.__setattr__`, swallowing rejection | §5.6 timing |
| 7 | Store the instance in the lifetime's cache; run `@post_construct` for request/transient lifetimes | PRD 03 |
| 8 | Return the instance | — |

Step 3's fallback chain is observable and worth stating once: a parameter with a default is a *soft*
dependency; a parameter without one is *hard*.

### 5.11 The zero-config default path

Three calls, no configuration:

```python run
import asyncio
from lauren_di import DIContainer, injectable

@injectable()
class Config:
    dsn = "sqlite:///app.db"

@injectable()
class ReportRepo:
    def __init__(self, cfg: Config) -> None:
        self.dsn = cfg.dsn

async def main() -> None:
    container = DIContainer()
    container.register(ReportRepo)
    container.register(Config)                     # order does not matter
    repo = await container.resolve(ReportRepo)     # first resolve validates the graph (D-14)
    assert repo.dsn == "sqlite:///app.db"

asyncio.run(main())
```

What "zero-config" guarantee covers:

| Guarantee | Meaning | Divergence from the reference |
|-----------|---------|-------------------------------|
| Default lifetime | `@injectable()` means singleton | none |
| No factory required | `register(cls)` inspects the class | none |
| No registration order | dependencies may be registered after consumers | none |
| No `compile()` required | first `resolve` validates once, then caches the compiled flag (D-14) | **diverges**: the reference requires an explicit `compile()`; without it a cycle recurses until `RecursionError` and a scope violation leaks silently |
| Re-validation after late registration | any `register*` after `compile()` clears the compiled flag (D-15) | **diverges**: the reference keeps `_compiled=True`, so a late provider is never validated |
| No module, no config object, no scope handle | singletons need neither a `Scope` nor an application object | none |

## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

| Reference symbol (file:line) | `lauren_di` home | Change |
|------------------------------|------------------|--------|
| `injectable` (`decorators.py:88`) | `_provider.py` | Keep signature and semantics; move the marker reader next to it |
| `reject_bare_usage` (`_metadata.py:61`) | `_provider.py` (private) | Extract only the function, not the metadata module |
| `InjectableMeta` (`_di/__init__.py:181`) | `_provider.py` | Unchanged (3 fields) |
| `_get_injectable_meta` (1585) | `_provider.py` → `injectable_meta()` | Public wrapper; marker name per D-11 |
| `register` (331), `_bind_token` (438) | `_container.py` | Keep; add target validation (D-16) and compiled-flag invalidation (D-15) |
| `_inspect_class_deps` (1813) | `_introspect.py` | Unchanged |
| `_resolve_class_signature_hints` (1869) | `_introspect.py` | Replace the stack walk with a documented, bounded version (see §6.6) |
| `_params_to_deps` (1968) | `_introspect.py` | Unchanged |
| `_inspect_callable_deps` (2013) | `_introspect.py` | Unchanged |
| `_extract_inject_token` (2058) | `_introspect.py` | Unchanged (`Inject` lives in `_custom.py`) |
| `_collect_field_deps` (2077) | `_introspect.py` | Drop the extractor branch; keep gates 1–6 |
| `_unwrap_annotated` (2165) | `_introspect.py` | Unchanged |
| `_looks_injectable` (2240) | `_introspect.py` | Drop `_is_extractor_marker`; `Depends`/`Inject` are the only markers |
| `_is_extractor_marker` (2313) | — | **Dropped** (product non-goal); replaced by the predicate seam D-9 |
| `_callable_default_map` (2332) | `_introspect.py` | Unchanged |
| `_construct_class` (2369) | `_container.py` | Unchanged behaviour; fix the docstring |
| `_filter_kwargs_for_signature` (2445) | `_introspect.py` | Unchanged |
| `_GeneratorContextWrapper` (104) | `_scope.py` | Move next to scope finalisation (PRD 03) |
| `Provider` (187) | `_provider.py` | Unchanged field set (PRD 01 §5.3.2) |

### 6.2 Parity table

Rule IDs are stable and are cited by PRD 05 (errors), 07 (overrides) and 08 (migration). "Observed"
records what the reference implementation actually did on 2026-11-21, not what its comments claim.

| Rule | Normative statement | Reference symbol | Observed reference behaviour | Test |
|------|--------------------|------------------|------------------------------|------|
| R-01 | `@injectable` without parentheses raises `DecoratorUsageError` | `injectable` → `reject_bare_usage` | raised at decoration time, message names the decorator | T02-01 |
| R-02 | `@injectable()` defaults to `Scope.SINGLETON`, `provides=()`, `multi=False` | `injectable` | marker holds exactly those defaults | T02-02 |
| R-03 | The decorator returns the target unchanged | `injectable` | `injectable()(X) is X` | T02-03 |
| R-04 | A target that refuses attribute assignment raises `DecoratorUsageError` | `injectable` (`setattr` guard) | `injectable()(int)` raised | T02-04 |
| R-05 | Metadata is not inherited; reading it off an undecorated subclass raises `MetadataInheritanceError` | `_get_injectable_meta` | raised at `register`/`injectable_meta`, message names both classes | T02-05 |
| R-06 | A re-decorated subclass is a normal provider | `_get_injectable_meta` | own `__dict__` marker wins | T02-06 |
| R-07 | `injectable_meta()` returns `InjectableMeta` or `None`, for classes and callables | `_get_injectable_meta` | `None` for `str` and for a lambda | T02-07 |
| R-08 | Registering a target without a marker raises `MissingProviderError` | `register` | "`Plain` is not marked @injectable" | T02-08 |
| R-09 | Registering a non-class non-callable target raises `InvalidProviderError` (D-16) | `register` (no check) | **silently installed a broken function provider** for `Dep()` | T02-09 |
| R-10 | Registering the same class twice raises `DuplicateBindingError` | `register` | raised | T02-10 |
| R-11 | Registering the same function twice raises `DuplicateBindingError` | `register` | raised | T02-11 |
| R-12 | A function provider's token is the function object and `factory` is the function | `register` | `cls is fn`, `factory is fn`, `is_function_provider=True` | T02-12 |
| R-13 | Function dependencies come from the function's signature, with the §5.5 rules | `_inspect_callable_deps` | `deps=(('d', Dep),)`; an unannotated param raised `UnresolvableParameterError` | T02-13 |
| R-14 | Generator functions set `is_generator_provider=True` | `register` and `register_factory` (`inspect.isgeneratorfunction`) | `True` for a `yield` function; async generators too | T02-14 |
| R-15 | A generator provider with `Scope.TRANSIENT` raises `LifecycleConfigError` at registration (D-17) | `register` and `register_factory` | raised the framework base `StartupError` | T02-15 |
| R-16 | Class deps come from `inspect.signature(cls)` | `_inspect_class_deps` | `__new__`-only and `__signature__` classes yield the same deps | T02-16 |
| R-17 | `self`, `cls`, `*args` and `**kwargs` are never deps | `_params_to_deps` | `deps=()` for a `*args, **kwargs` constructor | T02-17 |
| R-18 | An unannotated parameter without a default fails registration | `_params_to_deps` | `UnresolvableParameterError` with `detail={"target", "param"}` | T02-18 |
| R-19 | An unannotated parameter with a default is omitted from the plan | `_params_to_deps` | not in `deps`; the default applies | T02-19 |
| R-20 | An annotated parameter with a default is collected; a missing provider falls back to the default | `_instantiate` + `_callable_default_map` | `None` without a provider, the instance with one | T02-20 |
| R-21 | `Annotated[T, Inject(x)]` resolves by `x` in constructor and field position | `_extract_inject_token` | token `"DB_TOKEN"` in both positions | T02-21 |
| R-22 | Multiple `Inject` markers: the first wins, silently | `_extract_inject_token` | first marker used | T02-22 |
| R-23 | `Depends[T]` resolves by `T` in both positions **when `T` is a type**; a non-type `T` in field position is skipped by §5.6's gates 4/6 and is written `Annotated[T, Inject(tok)]` | `_unwrap_annotated` (reference); the library needs no unwrapping because `Depends[T] is T` (D-25) | field token was the inner function/class, which the library skips silently | T02-23 |
| R-24 | Field gate 1: only own annotations are collected | `_collect_field_deps` | inherited annotation produced no field dep | T02-24 |
| R-25 | Field gate 2: a class-body value excludes the field | `_collect_field_deps` | `d: Dep = None` → `field_deps=()` | T02-25 |
| R-26 | Field gate 3: dataclass fields are never DI fields | `_collect_field_deps` | `@dataclass` with `d: Dep` → `field_deps=()` | T02-26 |
| R-27 | Field gate 4: an unresolved string annotation is skipped silently | `_collect_field_deps` | `x: "Undefined"` → `field_deps=()` | T02-27 |
| R-28 | Field gate 6: primitives and non-`list` generics are skipped silently | `_looks_injectable` | `s: str`, `tags: list[str]` → `field_deps=()` | T02-28 |
| R-29 | Field gate 6: `@injectable` classes, runtime `Protocol`s and other user classes are collected | `_looks_injectable` | collected; a `Protocol` field resolved through `provides` | T02-29 |
| R-30 | Field gate 5: `Inject` short-circuits the type gate | `_collect_field_deps` | `db: Annotated[str, Inject("DB")]` collected as `"DB"` | T02-30 |
| R-31 | Field values are set after construction with `object.__setattr__`; assignment failure is swallowed | `_construct_class` | `hasattr(self, "d")` was `False` inside `__init__`; a `__slots__ = ()` class kept the attribute unset with no error | T02-31 |
| R-32 | Constructor kwargs are filtered against the class signature before the call | `_filter_kwargs_for_signature` | extra names dropped; `**kwargs` receives everything | T02-32 |
| R-33 | Registration order is free; `register` resolves nothing | `register` | consumer registered before its dependency, no error | T02-33 |
| R-34 | The first `resolve` validates the graph once; later `register*` invalidates that (D-14, D-15) | `compile` / `resolve` | **diverges**: the reference never compiled from `resolve` and never invalidated `_compiled` | T02-34 |
| R-35 | A positional-only parameter that would be collected is rejected at registration with `UnresolvableParameterError` (D-19) | `_params_to_deps` | **diverges**: the reference collected the dep and then dropped it — `TypeError: ... missing 1 required positional argument` when required, the parameter default (ignoring the registered provider) when it had one | T02-36 |

Additional coverage that does not map to a single rule (rounding out the contract):

| Case | Expected | Test |
|------|----------|------|
| Keyword-only parameter `def __init__(self, *, d: Dep)` | collected as a normal dep | T02-35 |
| Positional-only parameter that is *not* collected (`def __init__(self, verbose=False, /)`) | registered and constructed normally; the default applies | T02-36 |
| Class whose signature cannot be inspected (C extension) | zero deps, no error | T02-37 |
| `@injectable()` on a function with `provides=[T]` | `provides == (T,)`, token still the function | T02-38 |
| Field dep with no provider | `MissingProviderError` at validation, not at `register` | T02-39 |
| Generator with two `yield`s | first value returned; teardown runs only to the second suspension | T02-40 |
| Generator teardown | runs on scope exit / `shutdown()` (PRD 03) | T02-41 |
| `injectable_meta()` on an instance of a decorated class | returns the class's meta (reference behaviour, kept) | T02-42 |

### 6.3 Rejection rules and the exception each raises

Every rule that can reject input, with the exact exception. "Silent" rows are part of the contract too:
they are the cases a user will otherwise debug by reading source.

| Situation | Exception | Raised by | Detail keys | Test |
|-----------|-----------|-----------|-------------|------|
| `@injectable` without parentheses | `DecoratorUsageError` | `injectable` (decoration time) | `decorator`, `target` | T02-01 |
| Target refuses the marker attribute | `DecoratorUsageError` | `injectable` (decoration time) | — | T02-04 |
| Undecorated subclass reads inherited metadata | `MetadataInheritanceError` | `injectable_meta` / `register` | — (names both classes in the message) | T02-05 |
| Target not marked `@injectable` | `MissingProviderError` | `register` | — | T02-08 |
| Target is neither a class nor callable | `InvalidProviderError` (D-16) | `register` | `target`, `kind` | T02-09 |
| Target already registered | `DuplicateBindingError` | `register` | — | T02-10, T02-11 |
| Unannotated parameter, no default | `UnresolvableParameterError` | `register` | `target`, `param` | T02-18 |
| Positional-only parameter that would be collected | `UnresolvableParameterError` (D-19) | `register` | `target`, `param` | T02-36 |
| Generator provider with `Scope.TRANSIENT` | `LifecycleConfigError` (D-17) | `register` | `provider` | T02-15 |
| Collected dep with no provider | `MissingProviderError` | `compile` / `resolve` | `token` (PRD 05) | T02-39 |
| **Silent:** class-body default | — (field not collected) | — | — | T02-25 |
| **Silent:** dataclass field | — (field not collected) | — | — | T02-26 |
| **Silent:** primitive / non-`list` generic field | — (field not collected) | — | — | T02-28 |
| **Silent:** unresolvable string in field position | — (field not collected) | — | — | T02-27 |
| **Silent:** unresolvable string in parameter position | — (string token) | — | — | T02-43 |
| **Silent:** second `yield` in a generator provider | — (generator suspends) | — | — | T02-40 |
| **Silent:** field assignment rejected by the instance | — (attribute left unset) | — | — | T02-31 |
| **Silent:** unannotated parameter with a default | — (param not collected) | — | — | T02-19 |

`InvalidProviderError` is a new leaf under `DIError` (`code="invalid_provider"`); PRD 05 catalogues it.
`LifecycleConfigError` already exists in the reference with `code="lifecycle_config"`.

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | Graph validation trigger | explicit `compile()` only | first `resolve` compiles once, `compile()` stays public | a library cannot require the embedder to call `compile()`; without it a cycle is a `RecursionError` | D-14 |
| 2 | Registration after compile | `_compiled` stays `True`; late providers never validated | any `register*` clears the compiled flag | "compiled" must mean "the current graph was validated" | D-15 |
| 3 | Non-class non-callable target | silently installed as a function provider | `InvalidProviderError` | an unresolvable registration is a bug, not a feature | D-16 |
| 4 | Generator + `Scope.TRANSIENT` | `StartupError` | `LifecycleConfigError` | leaves live under `DIError`; PRD 01 §7 drops `StartupError` | D-17 |
| 5 | `resolve(..., framework_values=...)` | keyword name | `resolve(..., values=...)` | generic, transport-agnostic | D-13 |
| 6 | Extractor markers in field collection | `Path[int]`-style fields collected via `_is_extractor_marker` | markers are `Depends`/`Inject` only | no extractors in this library; predicate seam is D-9, PRD 08 | D-9 |
| 7 | Marker attribute name | `__lauren_injectable__` | renamed (D-11), not exported | avoid implying a framework dependency | D-11 |
| 8 | Positional-only dependency | collected, then `TypeError` at construction | `UnresolvableParameterError` at registration | the dep can never be passed by keyword; fail at the earliest point that can explain itself | D-19 |

Everything else in §6.2 is parity: same token, same exception class name (modulo the `DIError`
hierarchy), same ordering, same silent cases.

### 6.5 Module layout and import graph

```text
lauren_di/
├── _provider.py     injectable, InjectableMeta, injectable_meta, Provider  (registration records)
├── _introspect.py   _inspect_class_deps, _params_to_deps, _collect_field_deps,
│                    _looks_injectable, _unwrap_annotated, _extract_inject_token,
│                    _safe_type_hints, _safe_class_hints, _construct_class, _filter_kwargs_for_signature
├── _container.py    DIContainer.register / resolve / compile
├── _custom.py       Token, Inject, OptionalDep, CustomProvider, use_*      (PRD 04)
├── _scope.py        Scope, ResolutionScope, generator wrapper              (PRD 03)
├── _overrides.py    OverridePlan, ProviderOverride                         (PRD 07)
└── _errors.py       DIError and leaves                                     (PRD 05)
```

`_introspect.py` imports only `inspect`, `typing`, `sys`, `dataclasses` and `enum` — no `lauren`, no
third-party package. The forward-reference resolver (`_typing` in the reference) is reimplemented
in-tree (PRD 01 §6.4, D-3) because `typing_extensions` is not an allowed dependency.

### 6.6 Feasibility

- **The algorithms move unchanged.** `register` is 107 lines, the introspection family ~630 lines,
  `_construct_class` ~75. The only edits are the three divergences in §6.4 plus deleting the extractor
  branch; no algorithm is rewritten.
- **The stack walk is kept, with its cost documented.** `_resolve_class_signature_hints` and
  `_safe_type_hints` merge *every* frame's locals into `localns`, nearest frame first (`setdefault`
  gives that precedence). That is what makes a provider declared inside a test function resolve, so
  `lauren-di` keeps it unchanged rather than inventing a namespace parameter on every decorator. The
  cost is proportional to stack depth and is paid once per registration — registration is not a hot
  path. The alternative (requiring users to pass `localns=`) would be new public API for a problem the
  reference already solved.
- **Field collection is the only subtle part.** Five gates, all covered by T02-24…T02-30. The
  `__dataclass_fields__` check is what keeps `@dataclass` users safe (T02-26); dropping it would break
  every dataclass provider with a typed field.
- **Timing is cheap.** Fields are resolved before construction either way; the only difference is
  assignment order, so no extra pass is needed.
- **Risk: the `_looks_injectable` last resort.** Returning `True` for any user class means a typo'd
  annotation becomes a `MissingProviderError` instead of a silently unset attribute. That is the
  intended trade (fail loudly), but it is also why the primitive denylist must be exact: adding a type
  to `_NON_INJECTABLE_PRIMITIVES` changes what users must register.
- **Performance.** Registration does one `inspect.signature` per target and one hint resolution per
  class; resolution does dict lookups plus the cached `deps`. No hot-path allocation is added.

## 7. Error Catalog Impact

PRD 05 owns the catalogue (codes, message shapes, `detail` keys, remediation). PRD 02 fixes which
errors registration can raise. Every leaf named below is an exported symbol (D-18), so
`except DuplicateBindingError:` works as written and `except DIError:` remains the catch-all.

| Change | Detail |
|--------|--------|
| New leaf | `InvalidProviderError(DIError)`, `code="invalid_provider"`: `register()` received something that is neither a class nor a callable (D-16) |
| Reused leaf | `DecoratorUsageError` — bare decorator, unattachable marker |
| Reused leaf | `MetadataInheritanceError` — inherited marker |
| Reused leaf | `MissingProviderError` — undecorated target; missing dependency at validation |
| Reused leaf | `DuplicateBindingError` — token already registered |
| Reused leaf | `UnresolvableParameterError` — unannotated, default-less parameter |
| Reused leaf | `LifecycleConfigError` — generator provider with `Scope.TRANSIENT` (D-17) |
| Renamed from the framework | the reference raises `StartupError` for the generator/transient case; `StartupError` is not exported (PRD 01 §7) |
| Hierarchy | every leaf above is a direct or indirect subclass of `DIError`; none carries `status_code`/`headers`/`to_payload` |
| Not raised here | `CircularDependencyError`, `DIScopeViolationError`, `ProtocolAmbiguityError` — validation-time and collection-time, PRD 05 / PRD 04 |

Registration must never raise a bare `TypeError`/`AttributeError` for user error. The one place the
reference could (`inspect.signature` on exotic objects) is handled by returning no deps, which is the
documented behaviour (T02-37).

## 8. Phased Implementation Plan

PRD 01 §8 puts registration in phase 1 (`0.1.0`). Within phase 1:

| Step | Contents | Exit criteria |
|------|----------|---------------|
| 1 | `_provider.py`: `InjectableMeta`, `injectable`, `injectable_meta`, `Provider`, bare-usage rejection | T02-01…T02-07 green |
| 2 | `_introspect.py`: signature + parameter walk (`_inspect_class_deps`, `_params_to_deps`, `_inspect_callable_deps`, `_callable_default_map`) | T02-13, T02-16…T02-20, T02-32, T02-35…T02-37 green |
| 3 | `_introspect.py`: field collector (`_collect_field_deps`, `_looks_injectable`, `_unwrap_annotated`, `_extract_inject_token`) | T02-21…T02-30 green |
| 4 | `_introspect.py`: forward-reference resolver (in-tree `resolve_type_hints` + the two safe-hint helpers) | T02-27, T02-34, T02-43 green |
| 5 | `_container.py`: `register` for classes and functions, `_bind_token`, `_construct_class`, the single-provider `resolve` path | T02-08…T02-12, T02-31, T02-33, T02-38, T02-39, T02-42 green |
| 6 | Divergences: lazy compile (D-14), compiled-flag invalidation (D-15), target validation (D-16) | T02-09, T02-34 green; PRD 05's validation tests still green |
| 7 | Generator support: detection, setup advance, wrapper hand-off to PRD 03, transient rejection | T02-14, T02-15, T02-40, T02-41 green |

Steps 1–6 are shippable without scopes: a singleton-only container already satisfies the §5.11
quickstart. Step 7 depends on PRD 03's finalisation hook; until then the wrapper can be closed
explicitly in tests.

## 9. Documentation & Convention Updates

| Artefact | Update |
|----------|--------|
| `docs/guides/providers.md` | New page: §5.1, §5.5, §5.6, §5.7 verbatim examples; a "what is NOT a DI field" table from §5.6 |
| `docs/guides/generators.md` | Setup/teardown boundary, one-`yield` rule, transient rejection (§5.8) |
| `docs/guides/annotations.md` | Forward references, PEP 563, `Annotated[T, Inject(x)]`, `Depends[T]`, string tokens (§5.9) |
| `docs/reference.md` | `Provider` field table from §5.4 (generated from the dataclass, checked against the doc) |
| `docs/troubleshooting.md` | "Why is my field `None`?" → gates 2/3/4; "Why is `str` being resolved?" → gate 6; "Why does `__init__` not see the field?" → §5.6 timing |
| `llms-full.txt` | §5.5–§5.9 included; every `Provider` field documented (goal #11's checker enforces it) |
| `AGENTS.md` / `CLAUDE.md` | Conventions: one decorator, explicit registration, no extraction markers, field injection is best-effort, no auto-scanning |
| `CHANGELOG.md` | `0.1.0`: registration, constructor injection, field injection, function/generator providers |
| `CONTRIBUTING.md` | "New registration rule → new row in PRD 02 §6.2 + a test ID" |

Conventions recorded for later PRDs:

1. Every rejection rule added later gets an entry in §6.3 with its exception and a test ID.
2. Every new `Provider` field is additive and documented in §5.4 before it is used by another PRD.
3. Divergences are recorded in §6.4 with a `D-n`, never silently.
4. Registration APIs may not accept a target they cannot instantiate (the lesson of D-16).

## 10. Test Plan

Tests live in `tests/unit/` (registration/introspection) and `tests/parity/` (the same test bodies run
against `lauren._di` to prove parity). Every test uses only the public API plus `injectable_meta()` and
the returned `Provider` record.

### 10.1 Enumerated test cases

| ID | Test name | Setup | Assertion |
|----|-----------|-------|-----------|
| T02-01 | `test_bare_injectable_rejected` | `@injectable` on a class | raises `DecoratorUsageError`; message mentions `@injectable()` |
| T02-02 | `test_injectable_defaults` | `@injectable()` on a class | `meta.scope is Scope.SINGLETON`, `provides == ()`, `multi is False` |
| T02-03 | `test_decorator_returns_same_object` | `X = injectable()(X)` | returned object `is` the original; no wrapper |
| T02-04 | `test_unattachable_marker_rejected` | `injectable()(int)` | `DecoratorUsageError` |
| T02-05 | `test_metadata_inheritance_rejected` | decorated base, undecorated subclass | `injectable_meta(sub)` and `register(sub)` raise `MetadataInheritanceError` |
| T02-06 | `test_redecorated_subclass_registers` | subclass with its own `@injectable()` | registers; `provider.cls is Sub`; own scope wins |
| T02-07 | `test_injectable_meta_returns_none` | `injectable_meta(str)`, `injectable_meta(lambda: 1)` | both `None` |
| T02-08 | `test_register_requires_marker` | plain class | `MissingProviderError` mentioning `@injectable` |
| T02-09 | `test_register_rejects_instance` | `register(Decorated())` | `InvalidProviderError`; nothing is registered for the class token |
| T02-10 | `test_duplicate_class_registration` | register the same class twice | `DuplicateBindingError` |
| T02-11 | `test_duplicate_function_registration` | register the same function twice | `DuplicateBindingError` |
| T02-12 | `test_function_provider_record` | `@injectable()` on a function | `provider.cls is fn`, `factory is fn`, `is_function_provider is True`, `is_generator_provider is False` |
| T02-13 | `test_function_provider_deps` | function with `(cfg: Config)` and `(flag=True)` | `deps == (("cfg", Config),)`; unannotated default-less param raises `UnresolvableParameterError` |
| T02-14 | `test_generator_detection` | sync and async generator providers | `is_generator_provider is True` for both; `False` for a normal function |
| T02-15 | `test_generator_transient_rejected` | generator + `Scope.TRANSIENT` | `LifecycleConfigError`; `detail["provider"]` names it |
| T02-16 | `test_class_deps_use_class_signature` | classes with `__init__`, with `__new__`, and with a custom `__signature__` | all three produce the declared deps |
| T02-17 | `test_receiver_and_varargs_skipped` | `def __init__(self, *args, **kwargs)` | `deps == ()` |
| T02-18 | `test_unannotated_param_rejected` | `def __init__(self, repo)` | `UnresolvableParameterError`; `detail == {"target": ..., "param": "repo"}` |
| T02-19 | `test_unannotated_default_skipped` | `def __init__(self, verbose=False)` | not in `deps`; instance keeps the default |
| T02-20 | `test_annotated_default_is_optional` | `def __init__(self, d: Dep = None)` with and without `Dep` registered | `None` vs a `Dep` instance |
| T02-21 | `test_inject_token_constructor_and_field` | `Annotated[Connection, Inject("DB")]` in both positions | both resolve the `"DB"` value |
| T02-22 | `test_first_inject_marker_wins` | `Annotated[T, Inject("A"), Inject("B")]` | resolves `"A"`; no error |
| T02-23 | `test_depends_token_is_inner_type` | `Depends[Repo]` field and parameter; `Depends[make_dep]` field | the class spelling yields the `Repo` token in both positions; the function spelling is skipped (`field_deps == ()`), and `Annotated[Repo, Inject(make_dep)]` resolves `make_dep` |
| T02-24 | `test_inherited_annotation_not_collected` | base declares `d: Dep`, subclass does not | subclass `field_deps == ()` |
| T02-25 | `test_class_body_default_not_collected` | `d: Dep = None` | `field_deps == ()`; attribute stays `None` |
| T02-26 | `test_dataclass_fields_not_collected` | `@injectable() @dataclass` with `d: Dep`, `n: int = 0` | `field_deps == ()` |
| T02-27 | `test_unresolved_string_field_skipped` | `x: "Undefined"` | `field_deps == ()`; no exception |
| T02-28 | `test_primitive_and_generic_fields_skipped` | `s: str`, `n: int = 0`, `tags: list[str]`, `m: dict[str, int]` | all skipped |
| T02-29 | `test_injectable_protocol_and_user_class_collected` | field typed as a decorated class, a runtime `Protocol`, and an unregistered user class | first two injected; third collected and raises `MissingProviderError` at validation |
| T02-30 | `test_inject_token_short_circuits_type_gate` | `db: Annotated[str, Inject("DB")]` | `field_deps == (("db", "DB"),)` |
| T02-31 | `test_field_timing_and_assignment_safety` | `__init__` records `hasattr(self, "d")`; a `__slots__ = ()` class with `d: Dep` | `False` inside `__init__`, attribute present after; slotted instance left unset with no error |
| T02-32 | `test_kwargs_filtered_to_signature` | class accepting `d` only, provider deps include an unmatched name | call succeeds; no `TypeError` |
| T02-33 | `test_registration_order_is_free` | consumer registered before its dependency | resolve succeeds |
| T02-34 | `test_lazy_compile_and_invalidation` | cycle registered, `resolve` attempted; then `compile()`; then `register()` after `compile()` | `CircularDependencyError` on the first resolve (not `RecursionError`); registering afterwards makes the next resolve revalidate |
| T02-35 | `test_keyword_only_param_collected` | `def __init__(self, *, d: Dep)` | `deps == (("d", Dep),)` |
| T02-36 | `test_positional_only_param_rejected` | `def __init__(self, d: Dep, /)`; `def __init__(self, d: Dep = None, /)`; `def __init__(self, verbose=False, /)` | first two raise `UnresolvableParameterError` with `detail["param"] == "d"` (the reference instead raised `TypeError` / silently used the default); the third registers and resolves with the default intact |
| T02-37 | `test_uninspectable_class_has_no_deps` | a class whose `inspect.signature` raises | `deps == ()`; no error |
| T02-38 | `test_function_provides_binding` | `@injectable(provides=[Speaker])` on a function | `provides == (Speaker,)`; token is the function |
| T02-39 | `test_missing_field_provider_fails_validation` | field typed as an unregistered class | `register` succeeds; `compile()`/`resolve()` raise `MissingProviderError` |
| T02-40 | `test_generator_two_yields_suspends` | generator with two `yield`s | first value returned; teardown advances to the second suspension only |
| T02-41 | `test_generator_teardown_on_shutdown` | generator with a `finally` recording teardown | records setup at resolve, teardown after `shutdown()` (PRD 03) |
| T02-42 | `test_injectable_meta_on_instance` | instance of a decorated class | returns the class's `InjectableMeta` |
| T02-43 | `test_unresolvable_string_param_is_token` | `def __init__(self, x: "Undefined")` | `deps == (("x", "Undefined"),)`; resolving raises `MissingProviderError` naming it |

Property-style tests worth adding in the same phase:

- **Idempotent introspection** — registering the same class in two fresh containers yields equal
  `deps`/`field_deps` (guards against the stack walk leaking frame locals differently).
- **Field-collection totality** — for a generated matrix of annotation shapes (primitive, generic,
  optional, protocol, decorated class, string), `field_deps` membership matches the gate table exactly.

### 10.2 Parity harness

`tests/parity/test_prd02_registration.py` imports the container through a two-line shim:

```python fragment
# tests/parity/_impl.py
import os
if os.environ.get("LAUREN_DI_PARITY_TARGET") == "lauren":
    from lauren import injectable, Scope
    from lauren._di import DIContainer
else:
    from lauren_di import injectable, Scope, DIContainer
```

Every test in §10.1 is written against that shim and must pass for `lauren_di`; for `lauren`, the four
divergences (§6.4 rows 1–4) are marked `xfail(strict=True)` with the decision ID in the reason, so a
parity regression elsewhere cannot hide behind them. PRD 08 owns the full matrix.

### 10.3 Acceptance traceability for this PRD

| Acceptance clause | Where satisfied |
|-------------------|-----------------|
| Decorator semantics for classes and functions | §5.1, §5.7, R-01…R-07, T02-01…T02-07 |
| `DIContainer.register` | §5.3, R-08…R-12, T02-08…T02-12 |
| The `Provider` record and every field's meaning | §5.4 (18 fields + 2 properties), G4 |
| Constructor-parameter injection | §5.5, R-16…R-20, R-35, T02-16…T02-20, T02-35, T02-36 |
| Class-body field injection and its narrow collection rules | §5.6, R-24…R-31, T02-24…T02-31 |
| Function providers | §5.7, R-12, R-13, T02-12, T02-13, T02-38 |
| Generator providers with `yield` as setup/teardown boundary | §5.8, R-14, R-15, T02-14, T02-15, T02-40, T02-41 |
| Forward-reference and `Annotated[T, Inject(token)]` introspection | §5.9, R-21…R-23, T02-21…T02-23, T02-27, T02-43 |
| Zero-config default path | §5.11, R-33, R-34, T02-33, T02-34 |
| Parity table → source function + behaviour + enumerated test | §6.2 (35 rules, 43 tests) |
| Every rejection rule states its exception | §6.3 (17 rows, including the silent cases) |
