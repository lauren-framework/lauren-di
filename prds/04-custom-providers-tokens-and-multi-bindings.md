# PRD 04 — Custom Providers, Tokens & Multi-Bindings

| Field | Value |
|-------|-------|
| Status | Draft |
| Owner PRD | 04 |
| Depends on | 01 (symbol surface: `Token`, `Inject`, `OptionalDep`, `CustomProvider`, `use_*`), 02 (`Provider`, token rules, `provides`/`multi`), 03 (scopes, `ResolutionScope`) |
| Depended on by | 05 (error catalogue), 06 (visibility), 07 (overrides), 08 (migration) |
| Reference implementation | `../lauren-framework/lauren/_di/custom.py`, `lauren/_di/__init__.py` (`register_value`, `register_class`, `register_factory`, `register_alias`, `register_custom`, `resolve`, `_resolve_multi_list`, `resolve_one`), `lauren/exceptions.py` |
| Resolves | D-6 (`Depends[T]` representation) and adds D-25…D-30 |

This PRD specifies every way a dependency can be bound to something other than a decorated class: a
branded token, a literal value, a different implementation class, a factory, an alias, or one member
of a collection. It owns `Token`, `Inject`, `OptionalDep`, `Depends`, `CustomProvider`, the four
`use_*` recipes, the five `register_*` entry points they lower to, alias chains, and the `multi=True`
/ `list[T]` collection rules.

## 1. Problem Statement

### 1.1 Four recipes, one record, and a lowering table nobody wrote down

NestJS offers four "custom provider" recipes — `useValue`, `useClass`, `useFactory`, `useExisting` —
and the reference mirrors all four. Each helper returns the same frozen `CustomProvider` record with a
`kind` discriminator, and `register_custom` dispatches on that discriminator to a `register_*` method.
The record is clean; the *lowering* is the part with no specification. Six questions have no answer in
the docstrings:

1. Which `Provider` fields does each recipe set, and which stay `None`?
2. What token does the resulting provider answer to when `provide` differs from the class?
3. Does `use_class` require the target to be `@injectable`? (No — it auto-marks it.)
4. Are factory arguments passed positionally or by name? (Positionally, in `injects` order.)
5. What happens to an `OptionalDep` when the provider *is* present? (It resolves normally.)
6. Which errors does each rejection raise, and which are deferred until resolution?

Without a lowering table, an implementation gets 5 of 6 right and the sixth silently diverges. §5.4
is that table.

### 1.2 The reference returns a list from a scalar resolve

Probed against `../lauren-framework` on 2026-11-21, with two `multi=True` providers bound to `Svc`:

```
resolve(Svc)        -> [<A>, <B>]        # a list, from a scalar token
resolve(list[Svc])  -> [<A>, <B>]        # the same list
resolve(Svc)        -> <A>               # but with exactly one binding: not a list
```

Three behaviours in one call site. `resolve(Svc)` changes **return type** based on how many providers
happen to be registered: `Svc`, then `list[Svc]`, then — after one registration is removed — `Svc`
again. A caller cannot write a type annotation for that, and a test cannot assert a stable shape.
Code that works with one multi-binding breaks the day a second module adds one.

There is no NestJS precedent for this either: NestJS's container does not return arrays from a scalar
token. The coupling exists because the reference's `resolve` reuses the multi-binding path as a
convenience. §5.7 replaces it with a rule: **scalar means scalar; `list[T]` means collection**, and
more than one binding to a scalar token is an ambiguity error (D-26).

### 1.3 Aliases are scope-blind

`register_alias` creates a provider row with `scope=Scope.SINGLETON` as a placeholder, no factory, and
`alias_target` pointing at the real token. Resolution forwards to the target, so the *instance* is
correctly request-scoped. Validation does not follow the alias, though:

```
# singleton consumer, request-scoped target, reached through an alias
c.register(Req)              # @injectable(scope=REQUEST)
c.register_alias("ALIAS", Req)
c.register(Sink)             # @injectable() def __init__(self, x: Annotated[Req, Inject("ALIAS")])
c.compile()                  # no error        <- probe E-47
c.get_provider("ALIAS").scope  # Scope.SINGLETON, target is REQUEST  <- probe E-49
```

The same edge written directly — `def __init__(self, x: Req)` — raises `DIScopeViolationError` at
`compile()` (probe E-48). So an alias is a way to smuggle a request-scoped dependency into a
singleton: exactly the captured-dependency bug PRD 03 exists to prevent, reachable by adding one
indirection. §5.5 makes an alias transparent to validation (D-27): it carries no scope of its own, and
every consumer edge is checked against the terminal target.

### 1.4 Three different error shapes for one mistake

"Two providers are bound to one token that was not declared `multi=True`" is one mistake. The
reference reports it three ways depending on which code path noticed it first:

| Path | Exception | `detail` |
|------|-----------|----------|
| `compile()` eager scan of `_token_bindings` (probe E-61) | `ProtocolAmbiguityError` | `{"token": "Svc", "count": 2}` |
| `resolve(T)` (probe E-62) | `ProtocolAmbiguityError` | `{}` — empty |
| `resolve(list[T])` (probe E-33) | `ProtocolAmbiguityError` | `{"token": "list", "element": "Svc", "count": 1}` |

Two of the three cannot be acted on programmatically: one has no detail, and one names the *list*
token as `"list"` because `_describe(list[Svc])` loses the element. A caller who wants to report
"which token, which module, how many bindings" must parse the message. §5.8 and D-29 fix the shape:
one set of keys — `token`, `element` (for collections), `count` — in every path, including the eager
compile scan.

### 1.5 A token annotation resolves in `__init__` and vanishes in a class body

Both forms below are legal Python and look equivalent:

```python
DB = Token("DB")

@injectable()
class Works:
    def __init__(self, db: DB) -> None: ...      # resolves, probe E-57

@injectable()
class Silent:
    db: DB                                       # field never set, probe E-72
```

The constructor path treats the annotation as an opaque hashable token, so `db: DB` resolves. The
class-body path applies PRD 02's field gate ("an injected class, a runtime Protocol, or an
`Annotated[...]`"), a `Token` is none of those, and the field is dropped without a word. The instance
is constructed with no `db` attribute, and the failure surfaces later as `AttributeError` in user
code. §5.9 makes the field form explicit: `Annotated[T, Inject(token)]` works, and a bare `Token`
field is rejected at registration instead of being skipped (D-30).

### 1.6 `Depends[T]` has no meaning once the extractor layer is gone

D-6 is open: *"`Depends[T]`: extractor-style marker or plain alias"*. In the reference, `Depends` is
an `ExtractionMarker` subclass living in `lauren/extractors.py`; `Depends[Repo]` produces
`Annotated[Repo, Depends]`, and the DI collector only sees `Repo` because the *framework's* marker
stripping runs first. Probes E-50 to E-54 confirm both halves: the raw annotation carries the marker,
and the container resolves the inner token.

`lauren-di` has no extractor layer, no HTTP request and no marker-stripping pass (PRD 01 §3). Carrying
an `ExtractionMarker` subclass would be a lie about the dependency direction. §5.2 resolves D-6: the
`Depends` spelling is kept, but `Depends[T]` is `T` — a readable alias with no runtime marker, so
`param: Depends[Repo]` and `param: Repo` are indistinguishable to the container (D-25).

## 2. Goals

| # | Goal |
|---|------|
| G1 | `Token` gives identity-by-default keys, with `unique=False` as an explicit opt-in to name equality |
| G2 | `Inject` overrides the token of a constructor parameter or class-body field; `Annotated[T, Inject(x)]` is the canonical spelling and `T` remains for static checkers |
| G3 | `OptionalDep` makes one factory argument optional without weakening any other edge |
| G4 | All four `use_*` recipes lower to `Provider` rows through one documented table, and `register_custom` is exactly the dispatcher over the four `register_*` methods |
| G5 | An alias forwards to its terminal target, inherits the target's scope, is validated like a direct edge, and is rejected on self-reference |
| G6 | `multi=True` plus `list[T]` is the **only** way to obtain a collection, in registration order, with per-provider caching |
| G7 | Duplicate and ambiguous bindings raise one error shape with actionable `detail` in every code path |
| G8 | `Depends[T]` is a spelling-only alias for `T` (D-25), and every collection/alias/token rule is testable from the public API |

## 3. Non-Goals

- **No `providers=[...]` module arrays.** Registration is explicit (PRD 02 §5.5); a list of custom
  providers is a caller's list, not an API.
- **No `Sequence[T]`, `Iterable[T]`, `Collection[T]` or `tuple[T, ...]` collections.** Only `list[T]`
  is a collection request (the reference's `_multi_binding_element_type` is equally narrow); widening
  would capture unrelated generics. PRD 07 may add overrides per member, not new container shapes.
- **No value/class/factory recipe for modules or controllers.** `use_class` binds a token to a class;
  it does not install a module, a router or a lifecycle scheduler.
- **No extractor semantics on `Depends`.** No `Depends` marker, no request-parameter injection, no
  `ExtractionMarker` base class (PRD 01 §3, D-25).
- **No runtime-checkable Protocol scanning changes here.** Structural Protocol matching is PRD 02's
  `resolve_one` fallback; PRD 04 only says how a Protocol token participates in `provides` and
  `multi` (PRD 06 owns visibility filtering of the scan).
- **No `Token` subclasses or custom `__hash__`.** Three token kinds — class, string, `Token` — and a
  fourth is a breaking change to every message and `detail`.
- **No named/keyword factory arguments.** `injects` is positional (parity; §5.4).
- **No cross-container token sharing.** A `Token` is meaningful inside the container that registered
  it; `unique=False` shares a *name*, not a registry.

## 4. Prior Art

| System | Its custom-provider model | What `lauren-di` takes, and what it does not |
|--------|---------------------------|-----------------------------------------------|
| NestJS | `providers: [{ provide, useValue \| useClass \| useFactory \| useExisting, inject, scope }]`; `Symbol`/string tokens; factory `inject` array | The four recipe names, positional `inject`, and `multi` collections. Not the module array, not `inject` with `{ token, optional }` objects (we use `OptionalDep`), not request-scoped provider subtrees |
| `dishka` | Providers declared with `provide=`, `scope=`, and a factory returning the value | Confirms one record + one dispatcher. Its scope graph is richer than three scopes (PRD 03) |
| `injector` | `Module.configure(binder)`, `bind(Iface).to(Impl)`, `to_instance` | Confirms `bind(Iface).to(Impl)` is the core `use_class` idea; the binder DSL is rejected in favour of explicit `register_*` calls (PRD 02 §4) |
| `punq` | `register(service, factory, scope, instance)` with a single call shape | Confirms a small surface is enough; it has no alias or collection story, which is the gap this PRD fills |
| FastAPI `Depends` | A parameter marker resolved per request, not a provider declaration | The `Depends[T]` *spelling* users know, minus everything request-bound (D-25). No callable-as-dependency semantics, because there is no request to call into |
| Spring | `@Bean` methods with `@Qualifier` names; `@Primary` for ambiguity | The qualifier idea becomes a `Token`; Spring's `@Primary` is deliberately **not** carried — an ambiguity is an error, not a silent winner (D-26, §5.8) |

## 5. Proposed API

### 5.1 `Token`

```python
from lauren_di import Token

DB = Token("DB")                      # identity semantics
SHARED = Token("SHARED", unique=False)  # explicit name equality

assert Token("DB") != DB              # a fresh token is a different key
assert Token("SHARED", unique=False) == SHARED
```

| Aspect | Rule |
|--------|------|
| Constructor | `Token(name: str, *, unique: bool = True)`; `name` must be a non-empty `str` |
| Identity (`unique=True`, the default) | Two `Token` objects are equal only if they are the same object; hashing is object identity. Two `Token("DB")` are distinct dict keys |
| Equality (`unique=False`) | Equality and hash are `("lauren-token", name)`; two `Token("S", unique=False)` are the same key, across modules and processes |
| Mixed comparison | A unique token and a shared token are **never** equal, in either direction, even with the same name |
| `.name` / `.unique` | Read-only properties returning the constructor arguments |
| `repr` | `Token('DB', unique)` or `Token('SHARED', shared)` — the second word is the kind, so a log line says which semantics are in play |
| `__name__` | `"Token(DB)"`; this is what `_describe` renders in messages and `detail` |
| Hashability | Always hashable; usable as a dict key, set member, or annotation value |
| Invalid name | **`ValueError`**, not `DIError`: `Token("")` and `Token(123)` raise `ValueError("Token(name) requires a non-empty string name; got ...")` |

The `ValueError` is the one exception to D-18 in this PRD, and deliberately so: `Token` is a value
object that can be built at import time before any container exists, exactly like `int("x")`. A
container error class would imply a container operation that is not happening. `use_*`'s token
*validation* (§5.4) is a different case and does raise `DecoratorUsageError`.

Rules:

1. **Default is identity.** `Token("DB")` at each use site is a different dependency. Declare it once
   (`DB = Token("DB")`) and import it; that is the whole point.
2. **`unique=False` is a name, not a registry.** Two processes that both write
   `Token("SHARED", unique=False)` agree on the key because the name and the flag agree. Nothing is
   shared beyond that; a container still has to register a provider for it.
3. **A `Token` may be an annotation.** `def __init__(self, db: DB)` resolves (probe E-57), because a
   token is opaque and hashable. `Annotated[Conn, Inject(DB)]` is the recommended spelling (§5.9) —
   it keeps the static type meaningful.
4. **Tokens are not types.** `isinstance(x, DB)` and `class X(DB)` are errors; a token is a key.
5. **Exactly one token class.** Strings, classes and `Token`s are the three token kinds (PRD 02
   §5.9). Subclassing `Token` is not supported and is not detected.

### 5.2 `Inject`, `OptionalDep`, `Depends`

```python
from typing import Annotated
from lauren_di import Depends, Inject, OptionalDep, use_factory

DB = Token("DB")

@injectable()
class Repo:
    def __init__(
        self,
        conn: Annotated[Connection, Inject(DB)],   # resolve DB, type-check as Connection
        cache: Cache,                             # `Depends[Cache]` is Cache (D-25)
    ) -> None: ...

conn = use_factory(
    provide="CONN",
    factory=lambda opts, log: connect(opts, log),
    injects=[Options, OptionalDep("LOGGER")],     # missing LOGGER -> None
)
```

| Symbol | Form | Contract |
|--------|------|----------|
| `Inject` | `Inject(token: Any) -> InjectMarker` | A declaration marker for `Annotated[T, Inject(x)]`. The container resolves `x`; `T` is kept for static checkers only. The marker type is private (PRD 01 §5.3.6) |
| `OptionalDep` | `OptionalDep(token: Any)` | A frozen record with one field, `.token`. Only meaningful as a member of a `use_factory` / `register_factory` `injects` list, where a missing provider lowers to `None` |
| `Depends` | `Depends[T]` | **`Depends[T] is T`** (D-25). A spelling-only alias: it produces no marker, no wrapper and no runtime object |

Rules:

1. **`Inject` is annotation-only.** It is not a provider declaration and registers nothing. In an
   `injects` list, pass the bare token — `injects=[DB]`, not `injects=[Inject(DB)]`.
2. **First marker wins.** Two `Inject` markers on one annotation (`Annotated[T, Inject(a), Inject(b)]`)
   resolve `a`; PRD 02 §5.9 rule 2 owns this and PRD 04 does not change it.
3. **`Inject` may name a class that differs from the annotation type.** `Annotated[Iface, Inject(Impl)]`
   resolves `Impl` (probe E-64); this is the supported way to inject one implementation where the
   annotation is an interface.
4. **`OptionalDep` is not a marker and not a token.** Passing it as an annotation or as a `provide=`
   value is a programming error with no special handling: PRD 02's field gate skips it, and
   `use_value(provide=OptionalDep(...))` succeeds in creating a token nobody can inject. §5.9 lists
   the supported positions.
5. **`OptionalDep` never hides a scope violation.** The member's provider is still validated when it
   exists; optionality only covers *absence* (§6.3).
6. **`Depends` is a no-op.** `Depends[Repo]` and `Repo` are the same object, so the container's token
   rules (PRD 02 §5.9) apply unchanged: a class, a function token, or an `Annotated` override. No
   `ExtractionMarker` base class is carried, and `Depends` is not itself a token (D-25).

`Depends` is a single instance, not a class: `Depends[X]` subscribes the *instance* through
`__getitem__`, so the identity contract lives in one method and there is exactly one
subscription path. The private class deliberately does **not** define `__class_getitem__`:
it carried one that nothing reached — `Depends` is an instance and the class is not
exported, so `Depends[X]` never took that branch — and an unreachable branch on the single
path that must stay identity-only is a liability rather than a feature, so it was deleted
instead of being exercised (the decision and its coverage evidence are recorded in
`prds/evidence/42-depends-alias-dead-branch.md`). The whole contract is identity plus a
readable `repr`:

```python
class _DependsAlias:
    """Spelling-only alias: ``Depends[T] is T`` (D-25)."""

    def __getitem__(self, item: Any) -> Any:      # `Depends[X]` calls this
        return item

    def __repr__(self) -> str:
        return "Depends"


Depends = _DependsAlias()
```

Because it is a value and not a type, the spelling is **runtime-only**: `x: Depends[T]` is rejected
by both checkers, so a typed codebase writes `x: T`, or `Annotated[T, Inject(token)]` when the token
is not a type (PRD 01 §5.5). Every example in this PRD follows that rule.

### 5.3 `CustomProvider` and the four recipes

```python
from lauren_di import (
    CustomProvider, Scope, use_class, use_existing, use_factory, use_value,
)

# useValue
use_value(provide="SETTINGS", value={"debug": True})

# useClass
use_class(provide=Clock, use=FixedClock, scope=Scope.SINGLETON)

# useFactory
use_factory(provide="CONN", factory=connect, injects=[Options, OptionalDep("LOGGER")])

# useExisting
use_existing(provide="ALIAS", existing=Clock)
```

`CustomProvider` is a frozen dataclass — one shape, four meanings:

| Field | Type | Set by | Meaning |
|-------|------|--------|---------|
| `provide` | `Any` | all four | The token consumers inject; any hashable value (class, `str`, `Token`) |
| `kind` | `str` | all four | `"value"`, `"class"`, `"factory"`, `"existing"`; `register_custom` dispatches on it |
| `value` | `Any` | `use_value` | The literal bound to the token (`None` is a legal value) |
| `use_class` | `type \| None` | `use_class` | The class instantiated when the token resolves |
| `factory` | `Callable \| None` | `use_factory` | The callable invoked; may be sync or async |
| `inject` | `tuple[Any, ...]` | `use_factory` | Tokens passed **positionally**, in order; `OptionalDep` members lower to `None` |
| `existing` | `Any` | `use_existing` | The token whose instance this alias forwards to |
| `scope` | `Scope` | `use_class`, `use_factory` | Lifetime of the produced instance; `SINGLETON` default |
| `multi` | `bool` | `use_value`, `use_class`, `use_factory` | Whether the row joins a `list[T]` collection |
| `name` | `str` | all four | `"useValue"`, `"useClass"`, `"useFactory"`, `"useExisting"` — used in messages only |

Contract:

1. **Immutable.** Frozen; assigning a field raises `FrozenInstanceError`. A record can be built once
   at import time and registered into several containers (probe E-11).
2. **`kind` is validated in `__post_init__`.** An unknown kind raises `ValueError` naming the four
   legal values (probe E-12); this is the second and last `ValueError` in the PRD, for the same
   reason — a malformed value object.
3. **`scope` is ignored for `value` and `existing`.** A value is a singleton by construction; an alias
   has no scope of its own (§5.5 rule 2). `use_value` and `use_existing` therefore accept no `scope`
   argument at all.
4. **`multi=True` is accepted for `value`/`class`/`factory`, not for `existing`.** An alias binds a
   single name to a single target; `use_existing` has no `multi` parameter.
5. **Token validation happens at construction, not at registration.** All four helpers call
   `_validate_token(provide)` (and `existing`), so a `None` or unhashable token raises
   `DecoratorUsageError` with `detail={"token": repr(token)}` immediately (probe E-09).
6. **`use_class` requires a class and `use_factory` a callable**, both checked at construction:
   `DecoratorUsageError` with `detail={"use": ...}` / `detail={"factory": ...}` (probe E-17).
7. **`use_factory` validates every `injects` entry**, unwrapping `OptionalDep` first (probe E-09).

### 5.4 Registration entry points and the lowering table

Each recipe has a direct method as well as the record. `register_custom` is exactly the dispatcher
over the four; there is no other path into the container for a custom provider.

| Recipe | Lowers to | `Provider.provider_kind` | `factory` | Scope | Cached as |
|--------|-----------|--------------------------|-----------|-------|-----------|
| `use_value` | `register_value(provide, value, multi=...)` | `"value"` | closure returning `value` | `SINGLETON` (fixed) | token, or `id(provider)` when `multi` |
| `use_class` | `register_class(provide, use, scope=..., multi=...)` | `"class"` | the class itself | `scope` (default `SINGLETON`) | token, or `id(provider)` when `multi` |
| `use_factory` | `register_factory(provide, factory, inject=inject, scope=..., multi=...)` | `"factory"` | the factory callable | `scope` (default `SINGLETON`) | token, or `id(provider)` when `multi` |
| `use_existing` | `register_alias(provide, existing)` | `"existing"` | `None` | the terminal target's | never — no instance of its own |

The five methods, all specified here because they are the lowering targets:

| Method | Contract |
|--------|----------|
| `register_value(token, value, *, multi=False)` | Binds a literal. The value is placed in the singleton cache at registration time, so `resolve` never calls user code. `None` is a legal value |
| `register_class(token, cls, *, scope=SINGLETON, multi=False)` | Binds `token` to `cls`. `cls` need **not** be `@injectable`: if the marker is missing it is synthesised with the given scope (probe E-15), because the caller opted in by naming it. Its `__init__` and class-body deps are introspected as usual |
| `register_factory(token, factory, *, inject=(), scope=SINGLETON, multi=False)` | Binds `token` to `factory(*resolved_injects)`. Arguments are **positional**, in `inject` order, with synthetic parameter names `__factory_arg_<i>` (probe E-18). The factory is not decorated and its own signature is **not** introspected |
| `register_alias(token, existing, *, owning_module=None)` | Binds `token` to `existing` (§5.5). Rejects `token == existing` at registration (D-28) |
| `register_custom(custom, *, owning_module=None)` | Dispatches on `custom.kind` to the four above, forwarding `scope`, `multi` and `inject` |

Lowering rules that the table does not make obvious:

1. **`register_class` binds the token, not the class name.** `provider.cls` is `provide`, `provider.factory`
   is `use`. `resolve(provide)` constructs `use`; `resolve(use)` only works if `use` was itself
   registered.
2. **A value is already a cache entry.** `singletons()` contains the value immediately after
   `register_value` (probe E-13), and `resolve` returns the identical object (probe E-14). This is why
   `use_value` needs no scope and why `startup()` never touches it.
3. **A multi value does *not* occupy the token's singleton slot.** It is cached under `id(provider)`, so
   two `use_value(provide="V", multi=True)` rows both stay resolvable (probes E-37, E-74).
4. **Factory arguments are the only positional contract.** `OptionalDep` is the only wrapper; a
   missing optional member passes `None` (probe E-20), a present one passes its value (probe E-21),
   and a missing non-optional member raises `MissingProviderError` when the factory is first built
   (probe E-23).
5. **An async factory is awaited.** A factory returning an awaitable is awaited before caching
   (probe E-22); this is the only async-aware recipe besides decorated async function providers.
6. **`use_class` auto-marking is confined to that call.** It writes the injectable marker onto the
   class if absent; it does not register the class under its own token, and it does not change an
   existing marker's scope (PRD 02 owns marker precedence).
7. **`register_custom` performs no extra validation.** Everything is checked by the helper that built
   the record and by the `register_*` method; a hand-built `CustomProvider` with a valid `kind` but a
   missing payload (e.g. `kind="class"`, `use_class=None`) is a programming error that surfaces as
   `AssertionError`/`AttributeError` — construct records through the helpers.

### 5.5 Aliases

```python
from lauren_di import DIContainer, use_existing

container = DIContainer()
container.register(LoggerService)                  # the real provider
container.register_custom(use_existing(provide="AliasedLogger", existing=LoggerService))
container.register_alias("Legacy", "AliasedLogger")  # a chain

assert await container.resolve("Legacy") is await container.resolve(LoggerService)
```

Rules:

1. **An alias forwards; it never owns an instance.** The provider row has no factory and is skipped by
   the instance index (PRD 03 §5.9), so it never appears in `singletons()`, never runs a hook and
   never gets a teardown entry.
2. **An alias has no scope of its own.** Consumers are validated against the **terminal** target's
   scope, walking the chain (D-27). After validation, `get_provider(alias).scope` reports the
   terminal scope, so diagnostics do not claim `SINGLETON` for a request-scoped target (contrast
   probe E-49).
3. **Chains are followed to the terminal provider.** `A -> B -> C` resolves `C` (probe E-39); the
   chain is walked at validation, so a broken link is reported once.
4. **An unknown target is rejected at validation**, `MissingProviderError` with
   `detail={"alias": ..., "target": ...}` (probe E-41). Resolving an alias before validation reaches
   the target lookup and raises the plain `MissingProviderError` for the target (probe E-42) — the
   lazy-compile rule (D-14) makes the richer error appear on the first resolve.
5. **A cycle is rejected at validation.** `A -> B -> A` raises `CircularDependencyError` with
   `detail={"cycle": ["A", "B", "A"]}` (probe E-43); the chain participates in the same cycle
   detection as provider edges (PRD 05). Resolving before validation recurses (`RecursionError`,
   probe E-44) — the documented consequence of D-14, not a separate error path.
6. **A self-alias is rejected at registration.** `register_alias(t, t)` raises `DecoratorUsageError`,
   matching `use_existing`'s construction-time check (probe E-26), instead of the reference's
   compile-time `CircularDependencyError` (probe E-45) — D-28. A one-token cycle is always a typo and
   should fail where it is written.
7. **An alias token is unique.** Registering a second provider (or alias) under an alias's token raises
   `DuplicateBindingError` (probe E-46).
8. **`multi` is not part of an alias.** `use_existing` has no `multi` parameter; `register_alias` has
   none either. To contribute to a collection, register the real provider with `multi=True` and alias
   the collection's token if a second name is needed.
9. **Aliases inherit the target's lifetime but not its hooks.** A `@pre_destruct` on the target runs
   once, at the target's cache position; the alias adds nothing (PRD 03 §5.7).
10. **Aliasing a value is allowed** and is the cheapest way to expose one literal under two names; the
    value is already cached under its own token.

### 5.6 `multi=True` and `list[T]`

```python
from lauren_di import Scope, injectable

class Plugin: ...

@injectable(provides=[Plugin], multi=True)
class Audit(Plugin): ...

@injectable(provides=[Plugin], multi=True)
class Metrics(Plugin): ...

@injectable()
class Registry:
    def __init__(self, plugins: list[Plugin]) -> None:
        self.plugins = plugins

# resolve(list[Plugin]) -> [Audit instance, Metrics instance]   (registration order)
```

Where `multi` comes from:

| Registration | How to set it |
|--------------|---------------|
| `@injectable(provides=[T], multi=True)` on a class | marker → `Provider.multi`; `provides` binds `T` |
| `@injectable(provides=[T], multi=True)` on a function | same, token remains the function (PRD 02) |
| `register_class(..., multi=True)` | direct |
| `register_factory(..., multi=True)` | direct |
| `register_value(..., multi=True)` | direct |
| `use_value/use_class/use_factory(..., multi=True)` | via `register_custom` |
| `register_alias` / `use_existing` | **not supported** (§5.5 rule 8) |

Rules:

1. **`multi` marks a provider as a collection member; it does not change its token.** The row is still
   indexed under `provider.cls`/`provides`; it joins the token's binding list like any other.
2. **Duplicates are legal only when every binding is `multi`.** A second non-multi provider for a
   token raises `DuplicateBindingError` (probes E-35, E-71); any number of `multi=True` rows for one
   token are allowed (probe E-36).
3. **`list[T]` is the only collection request.** `Sequence[T]`, `Iterable[T]`, `tuple[T, ...]` and
   `set[T]` are not recognised and resolve as ordinary tokens (an error unless registered).
4. **The element may be any token kind**, including a `Token` (`list[DB]`, probe E-67) and a string
   (`list["V"]`, probe E-37).
5. **Order is registration order**, and stable across resolves (probes E-31, E-38). It is not sorted,
   not deduplicated and not dependent on module visibility order.
6. **Each member is cached independently**, under `id(provider)` for a multi row, so a singleton
   collection yields the same instances on every resolve (probe E-73) and a request-scoped collection
   yields one set per scope (probes E-76, E-77).
7. **`instance_index()` has one entry per multi provider**, keyed by that id (probe E-75), which is how
   teardown finds the right `pre_destruct_chain` for a multi slot (PRD 03 §5.9).
8. **A collection of zero members is an error, not an empty list.** `resolve(list[T])` with no
   provider for `T` raises `MissingProviderError` (probe E-32); an empty list would hide a
   registration mistake. If a caller wants "possibly empty", it injects `OptionalDep`-style through a
   factory (PRD 07 may add a collection override).
9. **Every member is scope-checked independently** against the consumer (PRD 03 §5.2, rule L-08).
10. **`list[T]` of a runtime Protocol** follows the same binding list; there is no structural scan for
    a collection. A Protocol with no explicit `provides` bindings raises `MissingProviderError`
    (PRD 02 owns the scalar structural fallback; PRD 06 owns its visibility).

### 5.7 The resolution matrix

Rows are what the caller asks for; the columns are what is registered for `T`. "Non-multi" means a
provider whose `multi` is `False`; "multi" means at least one row with `multi=True`.

| Registered for `T` | `resolve(T)` | `resolve(list[T])` |
|--------------------|--------------|--------------------|
| Nothing | `MissingProviderError` `{token}` | `MissingProviderError` `{token, element}` |
| Exactly one non-multi | the instance | **`ProtocolAmbiguityError`** `{token, element, count: 1}` — a non-multi row cannot be collected |
| Exactly one multi | the instance (`multi` does not change a scalar resolve) | a one-element list |
| Two or more multi | **`ProtocolAmbiguityError`** `{token, count}` — use `list[T]` (D-26) | a list, registration order |
| Any mix of multi and non-multi | **`ProtocolAmbiguityError`** `{token, count}` | **`ProtocolAmbiguityError`** `{token, element, count}` |

Reference behaviour, for the record (probes E-27 to E-34): one non-multi → instance; one multi →
instance; two multi → **a list**; mix → `ProtocolAmbiguityError`; `list[T]` identical except that a
non-multi member raises and an empty registration raises. The single intentional difference is the
"two or more multi" scalar cell (D-26); everything else is parity with a repaired `detail`.

Why the divergence: a return type that depends on how many providers happened to be registered is not
statically describable and turns a registration in another module into a runtime type change. The
collection shape already exists — `list[T]` — and making it mandatory keeps `resolve(T)` meaning
"exactly one `T`".

### 5.8 Duplicates and ambiguity

One mistake, one shape: every error in this table carries the same keys, whichever code path noticed
it first (D-29).

| Situation | Exception | `detail` | Raised at |
|-----------|-----------|----------|-----------|
| Second non-multi provider for a token | `DuplicateBindingError` | `{"token": ..., "existing": ..., "new": ...}` | registration |
| Second alias for a token | `DuplicateBindingError` | same | registration |
| Self-alias | `DecoratorUsageError` | `{"token": ..., "reason": "self_alias"}` | registration (D-28) |
| Two non-multi bindings (via `provides` or separate tokens) | `ProtocolAmbiguityError` | `{"token": ..., "count": 2}` | validation |
| Scalar resolve of a token with more than one binding | `ProtocolAmbiguityError` | `{"token": ..., "count": n, "hint": "resolve(list[T])"}` | resolve |
| `list[T]` where a member is not `multi` | `ProtocolAmbiguityError` | `{"token": "list[T]", "element": ..., "count": n}` | resolve |
| `list[T]` with no provider for `T` | `MissingProviderError` | `{"token": "list[T]", "element": ...}` | resolve |
| Alias target unknown | `MissingProviderError` | `{"alias": ..., "target": ...}` | validation |
| Alias cycle | `CircularDependencyError` | `{"cycle": [...]}` | validation |

Rules:

1. **`DuplicateBindingError` is a registration-time error.** It fires before the graph is validated, so
   the second call is the one that fails and the first registration is untouched.
2. **Ambiguity is never resolved silently.** There is no `@Primary`, no last-one-wins and no
   first-one-wins. A token with more than one non-collection binding is a graph error (D-26).
3. **`detail["token"]` is the caller's spelling**, rendered by `_describe`: `"SETTINGS"`,
   `Token('DB', unique)`, `list[Plugin]`. It is never the bare `"list"`.
4. **`count` is the number of visible bindings**, after PRD 06's visibility filter, so a module that
   sees one of two bindings is not ambiguous.
5. **`hint` is advisory.** It is a short string, present only on the scalar-multi case, and is not
   parsed by the library.

### 5.9 Annotation forms for tokens

| Position | `T` (class) | `Annotated[T, Inject(tok)]` | bare `Token` | `Depends[T]` | `OptionalDep(tok)` |
|----------|-------------|-----------------------------|--------------|--------------|--------------------|
| Constructor / `__new__` parameter | resolves `T` | resolves `tok` | resolves the token (probe E-57) | resolves `T` | not a token — `UnresolvableParameterError` if collected |
| Class-body field | resolves `T` if `T` is `@injectable`/Protocol | resolves `tok` (probe E-55) | **rejected at registration** (D-30) | resolves `T` (probe E-63) | ignored (not collected) |
| Function-provider parameter | resolves `T` | resolves `tok` | resolves the token | resolves `T` | not a token |
| `injects=[...]` entry | resolves `T` | pass the bare token instead | resolves the token | `Depends[T]` is `T` | optional member |

Rules:

1. **`Annotated[T, Inject(tok)]` is the canonical token form** in every position: the annotation keeps
   its static type and the marker carries the key.
2. **A bare `Token` annotation works in constructor/function position** because a token is opaque and
   hashable (probe E-57). It is accepted, not recommended, and it is the *only* position where a bare
   token annotation is accepted.
3. **A bare `Token` class-body field is rejected** with `UnresolvableParameterError` at registration
   and a message naming `Annotated[T, Inject(token)]` (D-30). The reference drops the field silently
   (probe E-72), leaving an instance without the attribute.
4. **`Depends[T]` is accepted anywhere `T` is accepted**, including class bodies (probe E-63), because
   it *is* `T` (D-25) — as a *runtime* expression. When `T` is a type the field is collected; when `T`
   is a function the annotation is not a type at all, so PRD 02 §5.6's field gates skip it silently
   and the typed spelling is `Annotated[T, Inject(fn)]` (PRD 01 §5.5).
5. **`OptionalDep` has exactly one supported position**: an `injects` list. Elsewhere it is either
   ignored by the field gate or an unresolvable token (§5.2 rule 4).
6. **A string token in an `injects` list needs no wrapper** (`injects=["LOGGER"]`); in an annotation
   it needs `Inject` (`Annotated[X, Inject("LOGGER")]`), because a string is not a type.

### 5.10 A complete program

Exercises every recipe, an alias chain, a collection and a token field, through public names only:

```python
import asyncio
from typing import Annotated

from lauren_di import (
    DIContainer, Inject, OptionalDep, Scope, Token,
    injectable, use_class, use_existing, use_factory, use_value,
)

DB = Token("DB")

class Clock:
    def now(self) -> str: ...

class FixedClock(Clock):
    def now(self) -> str: return "2026-11-21T00:00:00Z"

class Plugin: ...

@injectable(provides=[Plugin], multi=True)
class Audit(Plugin): pass

@injectable(provides=[Plugin], multi=True)
class Metrics(Plugin): pass

@injectable()
class Report:
    def __init__(
        self,
        clock: Clock,
        db: Annotated[str, Inject(DB)],
        plugins: list[Plugin],
    ) -> None:
        self.clock, self.db, self.plugins = clock, db, plugins

async def main() -> None:
    c = DIContainer()
    c.register_custom(use_value(provide=DB, value="sqlite://"))
    c.register_custom(use_class(provide=Clock, use=FixedClock))
    c.register_custom(use_factory(
        provide="CONN",
        factory=lambda url, log: (url, log),
        injects=[DB, OptionalDep("LOGGER")],   # LOGGER absent -> None
    ))
    c.register(Audit); c.register(Metrics); c.register(Report)
    c.register_custom(use_existing(provide="LegacyClock", existing=Clock))
    c.register_alias("OlderClock", "LegacyClock")

    report = await c.resolve(Report)
    assert report.clock is await c.resolve("OlderClock")
    assert report.db == "sqlite://"
    assert [type(p).__name__ for p in report.plugins] == ["Audit", "Metrics"]
    assert await c.resolve("CONN") == ("sqlite://", None)

asyncio.run(main())
```

Four things to notice, because they are the contract: the alias chain ends at the same singleton the
class token resolves; the `clock` parameter is an ordinary `Clock` dependency (`Depends[Clock]` would
be the same token, D-25); the `OptionalDep` member lowered to `None` without weakening the `DB` edge;
and the collection came back in registration order as a real `list`.

## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

| Reference symbol (file:line) | `lauren_di` home | Change |
|------------------------------|------------------|--------|
| `Token` (`custom.py:49`) | `_custom.py` | Unchanged semantics (identity default, `unique=False` name equality, `repr`, `__name__`) |
| `_InjectMarker` / `Inject` (`custom.py:128`/143) | `_custom.py` | Marker stays private; `Inject` unchanged |
| `CustomProvider` (`custom.py:186`) | `_custom.py` | Unchanged fields, including `name` for messages |
| `OptionalDep` (`custom.py:243`) | `_custom.py` | Unchanged frozen record |
| `Depends` (`extractors.py:315`) + `ExtractionMarker.__class_getitem__` (222) | `_custom.py` | **Replaced** (D-25): `Depends[T] is T`; no `ExtractionMarker` base, no `Annotated` wrapper |
| `_validate_token` (`custom.py:270`) | `_custom.py` | Unchanged: `DecoratorUsageError` for `None`/unhashable, `detail={"token": repr(...)}` |
| `use_value` / `use_class` / `use_factory` / `use_existing` (286/325/364/418) | `_custom.py` | Unchanged signatures; `use_existing` keeps the self-alias rejection |
| `register_value` (`_di/__init__.py:631`) | `_container.py` | Unchanged: value pre-cached as a singleton; `multi` caches under `id(provider)` |
| `register_class` (744) | `_container.py` | Unchanged, including marker synthesis for an undecorated `use` class |
| `register_factory` (806) | `_container.py` | Unchanged: synthetic `__factory_arg_<i>` deps, `optional_dep_names`, `positional_arg_names` |
| `register_alias` (866) | `_container.py` | **Extended** (D-27, D-28): self-alias rejected at registration; scope resolved from the terminal target at validation |
| `register_custom` (899) | `_container.py` | Unchanged dispatcher; the four-way branch and the `ValueError` for an unknown kind |
| `_bind_token` (438) | `_container.py` | Unchanged: appends to `_token_bindings`, invalidates `_instance_index` |
| `resolve` (1282) | `_container.py` | **Changed** (D-26): a scalar token with more than one visible binding raises instead of returning a list |
| `_resolve_multi_list` (1345) | `_container.py` → `_resolve_collection(token, element)` | Same algorithm; `detail` unified (D-29) |
| `resolve_one` (1012) | `_container.py` | Unchanged: `list[T]` expands to per-member edges; scope check per member |
| `_multi_binding_element_type` (144) | `_custom.py` | Unchanged; only `list[T]` / `typing.List[T]` is a collection |
| `_singleton_key` (inline, `_di/__init__.py:1430`) | `_container.py` | Kept as a helper: `id(provider)` for multi, `provider.cls` otherwise |
| `_instantiate` alias branch (1418) | `_container.py` | Unchanged: an `existing` row forwards to `resolve(alias_target)` |
| alias validation (`compile`, 1139–1151) | `_container.py` | Unchanged `MissingProviderError`/`CircularDependencyError`, plus the scope walk (D-27) |
| `instance_index` (1263) | `_container.py` | Unchanged: an `existing` row is skipped; a multi row is keyed by `id(provider)` |
| `ProtocolAmbiguityError` (exceptions.py:170), `DuplicateBindingError` (194), `DecoratorUsageError` (284), `UnresolvableParameterError` (200) | `_errors.py` | Re-based on `DIError`; `detail` unified (D-29) |

### 6.2 Custom-provider parity table

Rule IDs are stable and are cited by PRD 05 (errors), 06 (visibility), 07 (overrides) and 08
(migration). "Observed" records what `../lauren-framework` actually did on 2026-11-21, probed rather
than read from docstrings. `E-nn` citations point at probe scripts run against the reference, not at
rules: rule IDs are the `M-nn` values in the first column of this table, and the two namespaces never
share a number in a way that matters because they are always spelled `probe E-nn` or `M-nn`.

| Rule | Normative statement | Reference symbol | Observed reference behaviour | Test |
|------|--------------------|------------------|------------------------------|------|
| M-01 | A default `Token` compares and hashes by identity | `Token.__eq__`, `__hash__` | `Token("X") != Token("X")`; two dict keys | T04-01 |
| M-02 | `unique=False` compares and hashes by name | `Token.__hash__` | two `Token("S", unique=False)` collapse to one dict key | T04-02 |
| M-03 | A unique token never equals a shared token | `Token.__eq__` | false in both directions for the same name | T04-03 |
| M-04 | `.name`, `.unique`, `repr` and `__name__` are stable and readable | `Token` properties | `X / True`, `Token('X', unique)`, `Token(X)` | T04-04 |
| M-05 | A token name must be a non-empty string, else `ValueError` | `Token.__init__` | `ValueError` for `""` and `123` | T04-05 |
| M-06 | `use_value` lowers to a `"value"` provider whose factory returns the literal and whose value is cached immediately | `register_value` | `provider_kind="value"`, singleton, `singletons()` populated at registration | T04-06 |
| M-07 | A value resolves to the identical object, including `None` and mutable values | `register_value` | `resolve(...) is value` | T04-07 |
| M-08 | `use_class` lowers to a `"class"` provider: token `provide`, `factory=use`, scope honoured | `register_class` | `cls is provide`, `factory is use`, scope label matches | T04-08 |
| M-09 | A `use_class` target need not be decorated; the marker is synthesised | `register_class` | marker present after registration | T04-09 |
| M-10 | A `use_class` token resolves to the `use` type and caches per scope | `_instantiate` | `Impl` instance; same object twice in one scope | T04-10 |
| M-11 | `use_factory` lowers to a `"factory"` provider with synthetic positional dep names | `register_factory` | deps `__factory_arg_0..n`, `positional_arg_names` set | T04-11 |
| M-12 | Factory arguments are resolved positionally in `injects` order | `_instantiate` positional pass | `(cfg, log)` received in order | T04-12 |
| M-13 | A missing `OptionalDep` member passes `None` | `optional_dep_names` | `("only", None)` | T04-13 |
| M-14 | A present `OptionalDep` member passes its value | `optional_dep_names` | `("x", "found")` | T04-14 |
| M-15 | An async factory is awaited before caching | `_instantiate` | `"async-value"` | T04-15 |
| M-16 | A missing non-optional member raises `MissingProviderError` at first build | `_instantiate` | `No provider for NOPE` | T04-16 |
| M-17 | `use_existing` lowers to an `"existing"` row with `alias_target` and no factory | `register_alias` | `provider_kind="existing"`, `factory is None` | T04-17 |
| M-18 | An alias resolves to the target's instance, including request scope | `_instantiate` alias branch | `resolve("ALIAS") is resolve(Req)` inside one scope | T04-18 |
| M-19 | `use_existing` rejects a self-alias at construction | `use_existing` | `DecoratorUsageError` | T04-19 |
| M-20 | `None` and unhashable tokens are rejected by every helper | `_validate_token` | `DecoratorUsageError`, `detail={"token": ...}` | T04-20 |
| M-21 | `use_class` requires a class; `use_factory` requires a callable | `use_class`, `use_factory` | `DecoratorUsageError` with `use`/`factory` detail | T04-21 |
| M-22 | `CustomProvider` is frozen | dataclass `frozen=True` | `FrozenInstanceError` | T04-22 |
| M-23 | An unknown `kind` is rejected when the record is built | `CustomProvider.__post_init__` | `ValueError` naming the four kinds | T04-23 |
| M-24 | A scalar resolve with exactly one non-multi binding returns the instance | `resolve` | `One` | T04-24 |
| M-25 | A scalar resolve with exactly one multi binding returns the instance | `resolve` | instance, not a list | T04-25 |
| M-26 | `list[T]` with two multi bindings returns both, in registration order | `_resolve_multi_list` | `['A2', 'B2', 'C2']` | T04-26 |
| M-27 | `list[T]` with one multi binding returns a one-element list | `_resolve_multi_list` | `['A3']` | T04-27 |
| M-28 | A duplicate non-multi registration raises; duplicate multi registrations are allowed | `register_*` duplicate guard | `DuplicateBindingError`; three multi rows accepted | T04-28 |
| M-29 | `list[T]` with no provider for `T` raises `MissingProviderError` | `_resolve_multi_list` | raised | T04-29 |
| M-30 | `list[T]` with a non-multi member raises `ProtocolAmbiguityError` | `_resolve_multi_list` | raised | T04-30 |
| M-31 | `list[T]` accepts a `Token` element | `_multi_binding_element_type` | `[1, 2]` | T04-31 |
| M-32 | Multi members are cached independently: singleton identity, request per scope | `_singleton_key` | same objects twice; fresh per scope | T04-32 |
| M-33 | `instance_index()` has one row per multi provider | `instance_index` | two int keys | T04-33 |
| M-34 | An alias chain resolves to the terminal provider | alias validation | `A -> B -> C` resolves `C` | T04-34 |
| M-35 | An unknown alias target is reported with `alias` and `target` at validation | `compile` alias branch | `MissingProviderError {alias, target}` | T04-35 |
| M-36 | An alias cycle is a `CircularDependencyError` at validation | `compile` GRAY path | `{'cycle': ['A','B','A']}` | T04-36 |
| M-37 | A second registration under an alias token is a duplicate | `register_alias` guard | `DuplicateBindingError` | T04-37 |
| M-38 | An alias row owns no instance, no hook and no teardown entry | `instance_index` skip, `singletons()` | absent from `singletons()` | T04-38 |
| M-39 | `Inject` overrides the annotation type with a different token | PRD 02 token rule 1 | `Annotated[Iface, Inject(Impl)]` resolves `Impl` | T04-39 |
| M-40 | `Inject` works in constructor parameters and class-body fields, with class/string/`Token` keys | `_inspect_class_deps`, `_collect_field_deps` | body field resolves | T04-40 |
| M-41 | `Depends[T]` resolves `T`, including a function token | marker stripping | `Depends[Repo]` → `Repo`; `Depends[fn]` → `Other` | T04-41 |
| M-42 | A bare `Token` annotation resolves in constructor position | token lookup | `db: DB` → value | T04-42 |
| M-43 | Two non-multi providers bound to one token via `provides` fail validation | `compile` ambiguity scan | `ProtocolAmbiguityError {token, count}` | T04-43 |
| M-44 | `multi=True` is accepted by every registration entry point | `injectable(provides, multi)`, `register_*`, `use_*` | all five forms collect | T04-44 |
| M-45 | Multi values preserve order and identity | `register_value(multi=True)` | `["one", "two"]` | T04-45 |
| M-46 | A scalar resolve with **more than one** binding raises `ProtocolAmbiguityError`, never returns a list | `resolve` | **diverges**: returned `[A2, B2]` | T04-46 |
| M-47 | Alias edges are scope-checked against the terminal target and the alias reports that scope | alias validation, alias row `scope` | **diverges**: no check; row said `singleton` for a request target | T04-47 |
| M-48 | A self-alias is rejected at registration, not at validation | `register_alias` guard | **diverges**: `CircularDependencyError` at `compile()` | T04-48 |
| M-49 | Every duplicate/ambiguity error carries `{token, element?, count}` in every path | three separate raise sites | **diverges**: `{token,count}`, `{}` and `{token,element,count}` | T04-49 |
| M-50 | A bare `Token` class-body field is rejected at registration | field gate | **diverges**: silently dropped, `AttributeError` later | T04-50 |
| M-51 | `Depends[T]` is `T`, with no marker object | `Depends` via `ExtractionMarker` | **diverges**: `Annotated[T, Depends]` | T04-51 |

### 6.3 Rejection and containment rules

Every case that raises, and the cases that deliberately do not.

| Situation | Outcome | Produced by | `detail` | Test |
|-----------|---------|-------------|----------|------|
| `Token("")` / `Token(123)` | `ValueError` | `Token.__init__` | \u2014 (message only) | T04-05 |
| Unknown `CustomProvider.kind` | `ValueError` | `CustomProvider.__post_init__` | \u2014 | T04-23 |
| `provide=None` or unhashable | `DecoratorUsageError` | the `use_*` helper | `token` | T04-20 |
| `use_class(use=not_a_class)` | `DecoratorUsageError` | `use_class` | `use` | T04-21 |
| `use_factory(factory=not_callable)` | `DecoratorUsageError` | `use_factory` | `factory` | T04-21 |
| `use_existing(provide=t, existing=t)` | `DecoratorUsageError` | `use_existing` | token | T04-19 |
| `register_alias(t, t)` | `DecoratorUsageError` | `register_alias` | `token`, `reason` | T04-48 |
| Second non-multi binding for one token | `DuplicateBindingError` | every `register_*` | `token`, `existing`, `new` | T04-28 |
| Two non-multi bindings discovered at validation | `ProtocolAmbiguityError` | `compile()` | `token`, `count` | T04-43 |
| Scalar resolve, more than one visible binding | `ProtocolAmbiguityError` | `resolve()` | `token`, `count`, `hint` | T04-46 |
| `list[T]`, some member not `multi` | `ProtocolAmbiguityError` | `resolve()` | `token`, `element`, `count` | T04-30 |
| `list[T]`, no provider for `T` | `MissingProviderError` | `resolve()` | `token`, `element` | T04-29 |
| `resolve(T)`, no provider | `MissingProviderError` | `resolve()` | `token` | T04-24 |
| Alias target unknown | `MissingProviderError` | `compile()` | `alias`, `target` | T04-35 |
| Alias cycle / self-referential chain | `CircularDependencyError` | `compile()` | `cycle` | T04-36 |
| Missing non-optional factory member | `MissingProviderError` | first build | `token` (the member) | T04-16 |
| Missing `OptionalDep` member | **silent**: `None` | first build | \u2014 | T04-13 |
| Duplicate `multi=True` registrations | **silent**: both kept | all `register_*` | \u2014 | T04-28 |
| Alias of an alias | **silent**: chain walked | `resolve()` | \u2014 | T04-34 |
| Aliasing a value | **silent**: same cached object | `resolve()` | \u2014 | T04-38 |
| `list[T]` of a Protocol with two `provides` bindings | **silent**: both collected | `resolve()` | \u2014 | T04-44 |
| Empty collection request | `MissingProviderError`, never `[]` | `resolve()` | `token`, `element` | T04-29 |

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | `Depends[T]` representation | `Annotated[T, Depends]` with an `ExtractionMarker` subclass | `Depends[T] is T`, no marker | there is no extractor layer to strip markers; the marker would be dead weight and a false dependency direction | D-25 |
| 2 | Scalar resolve of a multi-bound token | returns a `list` when more than one binding | raises `ProtocolAmbiguityError` | a return type that depends on registration count cannot be annotated or tested; `list[T]` already spells "collection" | D-26 |
| 3 | Alias scope | alias row is `SINGLETON`; consumer edges through it are not checked | alias has no scope; edges are checked against the terminal target; the row reports that scope | an alias must not be a way to smuggle a request dependency into a singleton (PRD 03's core rule) | D-27 |
| 4 | Self-alias | accepted at registration, fails at `compile()` as a cycle | rejected at registration | a one-token cycle is a typo; failing where it is written is cheaper than at validation | D-28 |
| 5 | Ambiguity error detail | three shapes, one empty, one naming `"list"` | one shape: `token`, `element?`, `count`, `hint?` | the error must be actionable programmatically; parsing messages is not a contract | D-29 |
| 6 | Bare `Token` class-body field | silently dropped; `AttributeError` at first use | `UnresolvableParameterError` at registration | a silently missing attribute is the worst failure mode; the message names the fix | D-30 |
| 7 | Token name validation | `ValueError` | `ValueError` (unchanged) | a value object may be built before any container exists; `DIError` would imply a container operation | — |
| 8 | `CustomProvider.kind` validation | `ValueError` | `ValueError` (unchanged) | same reason | — |
| 9 | Logger/channel names in messages | `"lauren"` | `"lauren_di"` | one logger per distribution (PRD 03 §6.4 #9) | — |

Everything else in §6.2 is parity: same token semantics, same lowering, same alias forwarding, same
collection membership and order, same silent cases.

### 6.5 Module layout and import graph

```text
lauren_di/
├── _custom.py      Token, Inject (marker private), OptionalDep, Depends,
│                   CustomProvider, use_value/use_class/use_factory/use_existing,
│                   _validate_token, _multi_binding_element_type
├── _container.py   register_value/register_class/register_factory/register_alias/
│                   register_custom, resolve, _resolve_collection, _singleton_key
├── _provider.py    Provider (PRD 02): provider_kind, alias_target,
│                   optional_dep_names, positional_arg_names
├── _errors.py      DecoratorUsageError, DuplicateBindingError,
│                   ProtocolAmbiguityError, UnresolvableParameterError, ...
└── _introspect.py  (PRD 02) — nothing in this PRD touches it
```

Dependency direction: `_custom.py` imports `Scope`, the error leaves and nothing from `_container.py`;
`_container.py` imports `_custom.py`. `_custom.py` must stay importable on its own (it is the module a
user imports for `Token` and the recipes, and it must not drag the container in). There is no cycle.

### 6.6 Feasibility

- **The algorithms are small and already written.** `Token` is ~45 lines, the marker ~10,
  `_validate_token` ~15, each recipe ~20, the lowering ~40, the alias validation ~10, the collection
  path is `resolve_one` + `_resolve_multi_list` (~60 together). The work is relocation plus the six
  decisions above.
- **The single behavioural change is 5 lines in `resolve`**: replace the "all bindings multi and more
  than one → list" branch with a raise (D-26) and leave the one-binding path alone.
- **Alias scope resolution is one extra pass.** Before validating consumer edges, walk every
  `"existing"` row to its terminal provider and record that scope on the row (or in a side map); then
  `_check_scope` works unchanged because the alias row now reports the real scope. Cycle detection
  already runs and guarantees termination.
- **`detail` unification touches four raise sites** (`register_*` duplicate guard, the eager ambiguity
  scan, scalar resolve, `_resolve_collection`) and no algorithm. PRD 05 §5.4 owns the final key list;
  §5.8 is the contract.
- **Risk: `unique=False` across processes.** Name equality is only as good as the name. The docs must
  say "same name, same flag, same key" and warn against a shared flag with a mutable name (§9).
- **Risk: `id(provider)` cache keys.** A multi row's cache key is the provider's `id`, so it is stable
  only while the provider row is alive — which is the container's lifetime. A container that drops
  provider rows would leak cache entries; it does not (rows live in `_providers`), and PRD 03's
  teardown clears the cache on `shutdown()`.
- **Risk: scalar-multi divergence breaks a migrating app** that relied on `resolve(T)` returning a
  list. PRD 08 records it as a one-line migration (`resolve(T)` → `resolve(list[T])`) and the error's
  `hint` says the same thing at runtime.
- **Cost per collection resolve.** One list allocation per call, one `_instantiate` per member, no
  re-scan of the graph; the membership list is the `_token_bindings` entry, so nothing scales with the
  number of tokens.

## 7. Error Catalog Impact

PRD 05 owns codes, complete message shapes and remediation text. PRD 04 fixes which errors exist, when
they are raised, and the `detail` keys they must carry.

| Error | Who raises it here | Trigger | `detail` keys (this PRD) |
|-------|--------------------|---------|--------------------------|
| `DecoratorUsageError` | `Token`? no — `use_*`, `register_alias` | declaration misuse: `None`/unhashable token, non-class `use`, non-callable factory, self-alias | `token`/`use`/`factory`, optionally `reason` |
| `DuplicateBindingError` | every `register_*` | a second non-multi binding for one token | `token`, `existing`, `new` |
| `ProtocolAmbiguityError` | `compile()`, `resolve()` | more than one non-collection binding for a scalar token; a non-multi member in a collection | `token`, `element`, `count`, optionally `hint` |
| `MissingProviderError` | `resolve()`, first factory build, alias validation | no provider for a requested token or member; unknown alias target | `token`, or `alias`+`target`, or `element` |
| `CircularDependencyError` | `compile()` | alias cycle or provider cycle | `cycle` |
| `UnresolvableParameterError` | registration | a bare `Token` class-body field (D-30); the PRD 02 positional-only case (D-19) | `target`, `param`, `hint` (PRD 05 row 22) |

Not carried from the reference: `ValueError` stays `ValueError` for the two value objects (§5.1 rule
"Invalid name", §5.3 rule 2), and no HTTP-adjacent error appears. `DecoratorUsageError` is retained as
the declaration-time leaf rather than merged into `InvalidProviderError` (PRD 02 D-16), because the
failure is in the *declaration*, not in the provider target.

## 8. Phased Implementation Plan

| Phase | Work | Exit criterion |
|-------|------|----------------|
| 4a — Tokens and declarations | `Token`, `Inject` marker, `OptionalDep`, `Depends`, `_validate_token`, `CustomProvider` + its two `ValueError` guards | T04-01…T04-05, T04-19…T04-23 green; `from lauren_di import Token, Inject, OptionalDep, Depends` works with no container import |
| 4b — Recipes and lowering | `use_value/use_class/use_factory/use_existing`, `register_value/class/factory/alias/custom`, positional factory args, async factory, value pre-caching | T04-06…T04-18, T04-44, T04-45 green; every recipe has its worked example in the docs |
| 4c — Aliases and collections | alias chain walk, alias scope inheritance, self-alias rejection, `multi` binding, `list[T]` resolution, the matrix and unified `detail` | T04-24…T04-38, T04-43, T04-46…T04-50 green; the §5.7 matrix is a property test over every cell |

Ordering matters: 4b depends on 4a's record and validation; 4c depends on 4b's registration guards
(duplicate detection is the collection's precondition). PRD 05's catalogue cannot be written until 4c
fixes the `detail` keys, so 05 starts after this PRD is Draft.

## 9. Documentation & Convention Updates

- **New guide `docs/guides/custom-providers.md`**: the four recipes, one worked example each, and the
  lowering table from §5.4. States that `register_custom` is the dispatcher and the direct
  `register_*` form is equivalent.
- **New guide `docs/guides/tokens.md`**: identity by default, `unique=False` for cross-module sharing
  ("same name, same flag, same key"), `Annotated[T, Inject(token)]` as the recommended spelling, and
  the bare-token-annotation caveat (§5.9).
- **New guide `docs/guides/collections.md`**: `multi=True`, `list[T]`, registration order, per-member
  caching, and why an empty collection is an error.
- **Reference pages**: `Token`, `Inject`, `OptionalDep`, `Depends`, `CustomProvider`, the four
  `use_*` helpers and the five `register_*` methods, each with its signature from PRD 01 §5.3.
- **Convention**: every token used in an example is declared once at module level (`DB = Token("DB")`)
  and imported; no example constructs a token inline, because that is the bug `unique` exists to
  prevent.
- **Migration note (for PRD 08)**: `resolve(T)` returning a list becomes `resolve(list[T])`; the
  runtime error's `hint` names the replacement. `Depends` keeps its spelling and loses its marker.
- **`llms.txt` contract**: each symbol in §5.1–§5.4 must have one example in `llms-full.txt`, so the
  verifier in goal #11 can assert full coverage.

## 10. Test Plan

Tests live in `tests/unit/custom/` and `tests/parity/`. Everything below uses public names only:
`lauren_di` imports plus the `Provider` record returned by registration. Where a private map would be
convenient, the assertion uses `singletons()`, `instance_index()`, `registered_providers()` or the
`detail` of the raised error instead.

### 10.1 Enumerated test cases

| ID | Test name | Setup | Assertion |
|----|-----------|-------|-----------|
| T04-01 | `test_token_identity_by_default` | `Token("X")` twice | unequal; two dict keys |
| T04-02 | `test_token_shared_name_equality` | `Token("S", unique=False)` twice | equal; one dict key; equal hashes |
| T04-03 | `test_token_unique_vs_shared` | `Token("N")` vs `Token("N", unique=False)` | unequal in both directions |
| T04-04 | `test_token_props_and_repr` | one unique, one shared | `.name`, `.unique`, `repr`, `__name__` as specified |
| T04-05 | `test_token_invalid_name` | `Token("")`, `Token(123)` | `ValueError` each; no container involved |
| T04-06 | `test_use_value_lowering` | `register_custom(use_value(provide="V", value=1))` | `provider_kind == "value"`, singleton, `singletons()["V"] == 1` |
| T04-07 | `test_use_value_identity_and_none` | values `obj`, `None` | `resolve(...) is obj`; `resolve(...) is None` |
| T04-08 | `test_use_class_lowering` | `use_class(provide=Iface, use=Impl)` | `cls is Iface`, `factory is Impl`, default scope singleton |
| T04-09 | `test_use_class_auto_marks` | undecorated `Impl` | marker present after registration; no `@injectable` needed on the call site |
| T04-10 | `test_use_class_scope_and_identity` | request-scoped `use_class` | instance of `Impl`; same object twice inside one scope |
| T04-11 | `test_use_factory_lowering` | `injects=[A, "B"]` | deps named `__factory_arg_0/1`; `positional_arg_names` matches |
| T04-12 | `test_use_factory_positional_order` | factory recording its arguments | arguments arrive in `injects` order |
| T04-13 | `test_optional_dep_missing` | `OptionalDep("LOGGER")`, no provider | member is `None`; no error |
| T04-14 | `test_optional_dep_present` | provider registered | member is the resolved value |
| T04-15 | `test_async_factory_awaited` | `async def` factory | resolved value, not a coroutine |
| T04-16 | `test_factory_missing_member` | `injects=["NOPE"]` | `MissingProviderError` at first resolve |
| T04-17 | `test_use_existing_lowering` | alias of a class token | `provider_kind == "existing"`, `factory is None`, `alias_target` is the token |
| T04-18 | `test_alias_identity_and_scope` | request target + alias inside a scope | same object as the target; fresh in the next scope |
| T04-19 | `test_use_existing_self_alias` | `use_existing(provide="S", existing="S")` | `DecoratorUsageError` at construction |
| T04-20 | `test_token_validation` | `provide=None`, `provide=[...]` | `DecoratorUsageError` with `token` detail |
| T04-21 | `test_recipe_shape_validation` | non-class `use`; non-callable factory | `DecoratorUsageError` with `use`/`factory` detail |
| T04-22 | `test_custom_provider_frozen` | assign a field | `FrozenInstanceError` |
| T04-23 | `test_custom_provider_bad_kind` | `kind="bogus"` | `ValueError` naming the four kinds |
| T04-24 | `test_matrix_scalar_one_non_multi` | one plain provider | instance, not a list |
| T04-25 | `test_matrix_scalar_one_multi` | one `multi=True` provider | instance, not a list |
| T04-26 | `test_matrix_list_two_multi` | two multi providers | list of two, registration order |
| T04-27 | `test_matrix_list_one_multi` | one multi provider | one-element list |
| T04-28 | `test_matrix_duplicates` | duplicate non-multi; duplicate multi | `DuplicateBindingError`; multi accepted |
| T04-29 | `test_matrix_list_empty` | no provider for `T` | `MissingProviderError` with `token`+`element` |
| T04-30 | `test_matrix_list_with_non_multi` | multi + non-multi | `ProtocolAmbiguityError` with `token`+`element`+`count` |
| T04-31 | `test_matrix_list_of_token` | `list[DB]` with two multi values | `[1, 2]` |
| T04-32 | `test_multi_caching` | singleton and request multi rows | same objects twice; fresh per scope |
| T04-33 | `test_multi_instance_index` | two multi providers, then `instance_index()` | one row per provider |
| T04-34 | `test_alias_chain` | `A -> B -> C` | resolves the `C` instance |
| T04-35 | `test_alias_unknown_target` | alias to an unregistered token | `MissingProviderError` with `alias`+`target` at validation |
| T04-36 | `test_alias_cycle` | `A -> B -> A` | `CircularDependencyError` with `cycle` at validation |
| T04-37 | `test_alias_duplicate_token` | second registration under the alias token | `DuplicateBindingError` |
| T04-38 | `test_alias_owns_nothing` | alias of a singleton | absent from `singletons()`; no hook, no teardown entry |
| T04-39 | `test_inject_overrides_type` | `Annotated[Iface, Inject(Impl)]` | resolves `Impl` |
| T04-40 | `test_inject_positions` | constructor param, class-body field, string/`Token`/class keys | all four resolve the intended token |
| T04-41 | `test_depends_is_identity` | `Depends[Repo] is Repo`; ctor, body and function-token forms | true; all resolve |
| T04-42 | `test_bare_token_annotation_ctor` | `def __init__(self, db: DB)` | resolves the token's value |
| T04-43 | `test_validation_ambiguity_via_provides` | two non-multi `provides=[T]` | `ProtocolAmbiguityError` at `compile()` with `count == 2` |
| T04-44 | `test_multi_every_entry_point` | `@injectable(provides=...)`, `register_class/factory/value`, `use_class/factory/value` | each contributes one member |
| T04-45 | `test_multi_value_order_identity` | two multi values | `["one", "two"]`, stable across resolves |
| T04-46 | `test_matrix_scalar_many_bindings` | two multi bindings, scalar resolve | `ProtocolAmbiguityError` with `hint`; **never** a list |
| T04-47 | `test_alias_scope_checked` | singleton consumer of a request-scoped alias target | `DIScopeViolationError` at `compile()`; direct form also raises |
| T04-48 | `test_self_alias_registration` | `register_alias("S", "S")` | `DecoratorUsageError` at registration; nothing added to the graph |
| T04-49 | `test_ambiguity_detail_shape` | the four raise sites | every `detail` has `token` and `count`; collection errors add `element` |
| T04-50 | `test_bare_token_field_rejected` | `@injectable() class X: db: DB` | `UnresolvableParameterError` naming `Annotated[T, Inject(token)]` |
| T04-51 | `test_depends_no_marker` | `Depends[Repo]` | `is Repo`; no `Annotated`, no marker instance |

Property-style tests worth adding in the same phase:

- **Matrix totality** — for every `(registered shape, request shape)` cell in §5.7, generate the
  registration and assert either the documented value or the documented exception class *and*
  `detail` keys. Nine cells, one test parameterisation.
- **Alias transitivity** — for chains of length 1…5, `resolve(alias_0)` is the terminal instance and
  `singletons()` grows by exactly one entry.
- **Collection order invariance** — shuffling registration order permutes the resolved list in the
  same way; no other observable changes.

### 10.2 Parity harness

```python
# tests/parity/test_prd04_custom_providers.py
import pytest

from lauren_di import DIContainer, Token, use_value


@pytest.mark.parity
async def test_token_identity_matches_reference() -> None:
    first, second = Token("X"), Token("X")
    assert first != second
    assert len({first: 1, second: 2}) == 2


@pytest.mark.parity
async def test_value_recipe_matches_reference() -> None:
    container = DIContainer()
    provider = container.register_custom(use_value(provide="V", value=object()))
    assert provider.provider_kind == "value"
    assert await container.resolve("V") is container.singletons()["V"]


@pytest.mark.parity
@pytest.mark.xfail(strict=True, reason="D-26: scalar multi resolve raises instead of returning a list")
async def test_scalar_multi_diverges() -> None:
    ...
```

Rules M-46…M-51 are the divergent six; each carries `xfail(strict=True)` against `lauren` with the
decision ID in the reason, so removing the divergence flips the test and forces a PRD update. The other
45 rules run unchanged against both implementations.

### 10.3 Acceptance traceability for this PRD

| Acceptance clause | Where satisfied |
|-------------------|-----------------|
| **One worked example per recipe** | §5.3 (four snippets), §5.10 (all four plus alias, collection and token field), T04-06…T04-18 |
| **A resolution matrix for `T` vs `list[T]` vs multi-bound `T`, including error cases** | §5.7 five-row matrix plus the reference column; T04-24…T04-31, T04-43, T04-46, T04-49 |
| **Alias-chain and self-alias error behaviour specified** | §5.5 rules 1–10; §6.3 rows for unknown target, cycle, self-alias, duplicate alias token; M-34…M-37, M-47, M-48; T04-34…T04-38, T04-47, T04-48 |
| `Token` identity vs `unique=False` equality | §5.1; M-01…M-05; T04-01…T04-05 |
| `Inject` and `OptionalDep` semantics | §5.2, §5.9; M-13, M-14, M-39, M-40; T04-13, T04-14, T04-39, T04-40 |
| `CustomProvider` and the four recipes lower to `Provider` | §5.3, §5.4; M-06…M-23; T04-06…T04-23 |
| `multi=True` and duplicate-binding rules | §5.6, §5.8; M-26…M-33, M-43…M-45; T04-26…T04-33, T04-44, T04-45 |
| `Depends[T]` resolved (D-6) | §5.2 rule 6; D-25; M-41, M-51; T04-41, T04-51 |
| Every rule testable from the public API | §10 preamble; assertions use `singletons()`, `instance_index()`, `registered_providers()` and error `detail` only |
