# PRD 06 — Module Visibility: Decision Record and Design

| | |
|---|---|
| **Status** | Draft — 2026-11-21 |
| **Depends on** | 02 (`Provider`, registration entry points), 05 (`compile()` phases, error catalogue, `D-n` decisions) |
| **Resolves** | **D-5** — "Module encapsulation: v1 or v2" |
| **Introduces** | D-37 … D-43 |
| **Rules** | MV-01 … MV-40 |
| **Tests** | T06-01 … T06-40 |
| **Probes** | 19 numbered observations, `E-201` … `E-225`, plus `E-208c` and `E-215b`, run against `../lauren-framework` on Python 3.13; every cited id is indexed in §6.6, and the six ids in that range with no observation are listed there as reserved |

---

## 1. Problem Statement

`lauren-di` must decide, once and in writing, whether the container understands
*who declared a provider*, or whether every provider is visible to every caller.

The question is not academic. The reference implementation already ships the
machinery: `Provider.owning_module`, `DIContainer.set_visible(...)`, an
`owning_module=` keyword on nine registration methods and three resolution
methods, and a visibility filter inside the resolver. `lauren-di`'s public API
table (PRD 01 §5.3) already lists every one of those parameters, because the
migration target (PRD 08) has call sites that pass them. The only open question
is what the parameters *do*.

Three facts make the decision urgent rather than deferrable:

1. **An accepted-but-ignored parameter is a silent behaviour change.** In
   `lauren`, `resolve(Token, owning_module=M)` is filtered; in a `lauren-di` that
   ignores the argument it would widen to the whole container. A migrator's
   controller would silently acquire dependencies it was never granted. PRD 08's
   parity suite would have to encode that difference as *expected*, on a
   security-relevant axis.
2. **The concept is small; the graph is not.** Visibility is three predicates
   and a dictionary (§46 lines of reference code, `_is_visible` 962–975 plus the
   three call sites). The NestJS *graph* — `@module`, `imports`, `exports`,
   controllers, transitive re-export, module substitution — is a different and
   much larger thing, and PRD 01 §3 already excludes it.
3. **The reference's implementation has four defects that a standalone library
   cannot ship unchanged.** They are measured, not hypothetical, and each one is
   a place where "copy the reference" produces a library that leaks.

### 1.1 The reference couples visibility to a graph the library will not have

`set_visible` is never called by hand in the framework. It is called once per
module by the ASGI startup path:

```python fragment
# lauren/_asgi/__init__.py:4128
for mod_cls in graph.modules:
    container.set_visible(mod_cls, graph.visible_in(mod_cls))
```

`graph.visible_in(M)` (`lauren/_modules/__init__.py:345`) returns the module's
own provider tokens plus everything re-exported by a transitively imported
module (`probe E-218`: `Core → {SvcA}`, `App → {SvcA, SvcB}`). So the framework's
visibility is *derived*: the user writes `imports`/`exports`, the graph computes
sets, the container consumes them.

`lauren-di` has no graph and will not get one (PRD 01 §3, "The module-graph
compiler"). If visibility is to exist here at all, it must be a **declared**
set rather than a derived one: the caller says what each module may see. That is
strictly less convenient and strictly more explicit — which is the same trade
this PRD set makes everywhere else (`@injectable()` must be called, no
auto-discovery, no implicit scanning).

### 1.2 Defect: the container fails *open* for undeclared modules

`_is_visible` returns `True` when the consumer's module has no entry in
`_visible` (`_di/__init__.py:966–968`):

```python fragment
def _is_visible(self, owning_module):
    visible = self._visible.get(owning_module)
    if visible is None:
        # No explicit visibility installed — legacy / unit-test mode.
        return True
```

`probe E-203` measures the consequence: with `ModA` and `ModB` declared and
`ModC` never declared, `resolve(SvcB, owning_module=ModC)` returns the instance
that belongs to `ModB`. Forgetting one `set_visible` call silently disables
encapsulation for that module — and nothing fails, so nothing tells you.

Fail-open is defensible in a framework, where the startup path enumerates every
module and the "absent entry" case only happens in unit tests. It is not
defensible in a library whose users wire modules by hand, because the failure
mode is exactly the leak the feature exists to prevent.

### 1.3 Defect: compile-time ambiguity ignores module boundaries

The reference validates ambiguity over the flat token table, twice: once over
edges (`resolve_one`, `_di/__init__.py:1105`) and once eagerly over every token,
whether or not anyone depends on it (`_di/__init__.py:1193–1205`). Neither pass
knows about `_visible`.

`probe E-207` measures the result. Two modules each bind one implementation of
the same structural Protocol — the canonical encapsulation case, and the one the
framework's own test suite builds:

```python fragment
@injectable(provides=[Store])
class StoreA: ...
@injectable(provides=[Store])
class StoreB: ...

container.register(StoreA, owning_module=ModA)
container.register(StoreB, owning_module=ModB)
container.set_visible(ModA, frozenset({StoreA, Store}))
container.set_visible(ModB, frozenset({StoreB, Store}))
container.compile()
# ProtocolAmbiguityError: Multiple bindings for Store
# detail {'token': 'Store', 'count': 2}
```

Each module sees exactly one binding, so nothing is ambiguous from any caller's
point of view — yet `compile()` refuses. Encapsulation exists so that per-module
implementations can coexist; a validator that forbids the pattern before anyone
resolves is validating the wrong graph.

### 1.4 Defect: grants are keyed on the concrete class, not on the bound token

`_is_visible` compares `provider.cls in visible` (`_di/__init__.py:970`). For a
class provider declared `@injectable(provides=[Sender])`, `provider.cls` is the
*concrete* class (`Smtp`), while the token table binds two names to the row
(`probe E-217`: `['Smtp', 'Sender']`).

`probe E-215` measures the consequence: granting the protocol token hides the
provider that is registered under it.

```python fragment
container.set_visible(ModA, frozenset({Sender}))   # "ModA may use senders"
container.resolve(list[Sender], owning_module=ModA)
# MissingProviderError: No provider for Sender (requested as list) visible from module ModA
# detail {'token': 'list', 'module': 'ModA'}
```

Granting `{Sender, Smtp}` instead works (`probe E-216`: ModA gets `['Smtp']`,
ModB gets `['Ses']`). So the feature is usable only if every caller remembers to
name concrete classes alongside the tokens it actually means — and the error
message names `list` rather than `list[Sender]`, because the reference's renderer
drops type arguments (PRD 05 D-33, `probe E-101`).

### 1.5 Defect: re-declaring visibility after `compile()` skips validation

`set_visible` mutates `_visible` but does not clear `_compiled`
(`_di/__init__.py:952–960`). `probe E-225` measures the window: compile with
`ModB` granted nothing, then `set_visible(ModB, {SvcA})`, then resolve —
`ModB` now sees `SvcA`, while `_compiled` is still `True` and no validation ran
against the new domain.

Widening after validation is how a graph that compiled cleanly ends up serving a
dependency nobody checked. PRD 05 §5.1 already requires `register*` to invalidate
compilation; visibility declarations belong in the same category.

### 1.6 What is actually being decided

Four questions, and the rest of this PRD answers them in order:

| # | Question | Answer |
|---|----------|--------|
| 1 | Does `lauren-di` ship module visibility at all, and in which version? | v1, active (§5.1) |
| 2 | What does `owning_module=None` mean? | The flat registry when nothing is declared; the global view afterwards (§5.6) |
| 3 | How is a visible set expressed without a module graph? | Declared per module, `set_visible` / `grant_visible`, row-addressable (§5.4) |
| 4 | Does the declarative graph ever ship? | Not in v1, and not planned for v2 (§5.10) |

---

## 2. Goals

| # | Goal | Test |
|---|------|------|
| G1 | An explicit, unambiguous decision: visibility primitives ship in v1 and are functional | §5.1; T06-40 |
| G2 | `owning_module` is recorded on every `Provider` and accepted by every registration entry point | T06-02, T06-03, T06-05 |
| G3 | With no visibility declaration the container behaves exactly like the flat registry of PRDs 02–05 | T06-21 |
| G4 | A declared module sees its own providers, global providers, and explicitly granted rows — nothing else | T06-01, T06-06, T06-07, T06-08 |
| G5 | Unlawful access raises a typed error that names the token *and* the module, and never claims the token is unregistered | T06-16 |
| G6 | An undeclared module fails closed with a fix-it hint, never silently widening | T06-17 |
| G7 | Compile-time ambiguity is evaluated per visibility domain, so per-module implementations compile | T06-24 |
| G8 | Structural-Protocol scanning is scoped to the consumer's domain | T06-30, T06-32 |
| G9 | Multi-binding collections are filtered per domain, so a grant on a token never leaks a sibling row | T06-27, T06-28 |
| G10 | Aliases cannot tunnel: an alias row never grants access to a target the domain cannot see | T06-29 |
| G11 | `explain()` reports, per row, who declares it and why it was excluded | T06-33, T06-35 |
| G12 | Visibility declarations invalidate `compile()`, so validation never runs against a stale domain | T06-26 |
| G13 | The declarative module graph is explicitly out of scope, in v1 and in v2, with the reason recorded | T06-38, T06-39 |
| G14 | The compatibility guarantee is stated in terms a test can enforce | T06-40 |

## 3. Non-Goals

- **`@module`, `imports`, `exports`, `controllers`, `host`, `name`.** The
  declarative graph is a framework concern and stays out permanently (§5.10
  explains why v2 does not change that).
- **Derived visibility.** No API computes a visible set from an import graph.
  Visible sets are declared (§5.4). A caller that wants derivation writes the
  loop itself, exactly as `_asgi/__init__.py:4128` does.
- **Controllers and controller privacy.** The reference keeps controllers
  private to their module (`visible_in`, `_modules/__init__.py:356–359`). There
  are no controllers here, so there is nothing to keep private. Route-bearing
  classes are ordinary providers and follow MV-05 … MV-09 unchanged.
- **Module-scoped configuration or request state.** Visibility filters *which
  provider row* a resolution may use. It does not introduce a per-module cache,
  a per-module settings object, or per-module scope semantics.
- **Module-level override substitution.** PRD 07's overrides are per token and
  per build (D-12). `OverridePlan.modules` from the reference is not carried.
- **Entry-point scanning, dynamic modules, `for_root` / `for_feature`,
  lazy modules, global modules.** All of these are graph features.
- **Thread- or task-local module identity.** The module argument is a plain
  parameter on the call (PRD 07 owns ContextVar scoping for overrides, not for
  visibility).
- **Any new exception class.** Visibility reuses `MissingProviderError`,
  `ProtocolAmbiguityError` and `DecoratorUsageError` (§7).

## 4. Prior Art

| System | What it does | What `lauren-di` takes, and what it rejects |
|--------|--------------|---------------------------------------------|
| **NestJS** | `@Module({ providers, imports, exports })`; a provider is visible to its module and to importers of an exporting module; every injection is resolved through the consumer's module | Takes the *semantics* (own + imported-exports, transitive only through explicit re-export, `probe E-224`) and rejects the declarative graph |
| **Angular** | `NgModule({ providers, exports })`, hierarchical injectors, `providedIn: 'root'` for globals | Takes "a provider with no owner is global" as `owning_module=None`; rejects the injector hierarchy — there is one container, and domains are views on it |
| **Python `injector`** | `Module` classes bind interfaces to implementations; `Injector(modules=[...])` merges them | Rejects module-as-binding-bundle: bindings already exist in PRD 04. Modules here are *permission domains*, not registration units |
| **`dependency-injector`** | Container hierarchy and `providers.Container` overrides; no visibility filters | Confirms that hierarchy is the usual answer and that it is orthogonal: this design keeps one flat registry and filters per call |
| **Pytest fixtures / `pytest-django`** | Fixture visibility is file- and conftest-scoped; resolution walks a directory tree | The closest ergonomic precedent for "nearest declaration wins". Rejected because it needs a filesystem convention; `lauren-di` has no discovery |
| **Python access control (`_private`)** | Convention only, no runtime check | Shows the cost of convention-only scoping: it is not testable. MV-13 raises instead |

The distinguishing choice is that every other system derives visibility from a
declaration structure the *framework* owns. `lauren-di` declares the sets
directly and derives nothing, because it owns no structure to derive from.

## 5. Proposed API

### 5.1 The decision

> **D-37 (resolves D-5).** `lauren-di` **v1 ships module visibility as a
> first-class, functional feature**: `Provider.owning_module`, the
> `owning_module=` keyword on every registration and resolution entry point,
> `set_visible` / `grant_visible`, visibility-filtered resolution, and
> visibility-scoped structural-Protocol scanning. The **declarative module
> graph** (`@module`, `imports`/`exports`/`controllers`, `ModuleGraph`,
> substitution) **is not part of v1 and is not scheduled for v2**; it remains a
> framework concern (PRD 01 §3). The seam is therefore *active*, not inert: the
> "inert in v1" branch recorded in D-5 is rejected, and §5.9 preserves the exact
> contract that branch would have had, in case a later re-scoping reverses this
> decision.

The decision is binary and complete: primitives **in**, graph **out**, for v1
and beyond. A reader who needs the one-sentence version: *`lauren-di` knows who
owns a provider and will enforce it on request; it does not know what a module
declares.*

**Why v1 rather than v2** (the three tests the register asks of any `D-n`):

| Criterion | Assessment |
|-----------|------------|
| Cost of shipping | ~150 lines: one dict of grants, three predicates (`visible_rows`, `_row_visible`, `_domain_for`), one filter in `resolve_one`, domain-aware ambiguity in `compile`, `explain`. No new module, no new dependency |
| Cost of *not* shipping (v2 deferral) | `owning_module` must still be accepted on nine registration methods and three resolution methods, or PRD 08's mapping breaks; an accepted-and-ignored parameter is a silent semantic change on a security axis (§1.1) |
| Blast radius | Zero for users who never call `set_visible`: with no declaration, every predicate short-circuits to "visible" and the container is the flat registry of PRDs 02–05 (MV-08, T06-04) |

**Reversibility cost.** The v1 surface is additive (`owning_module` already
exists in PRD 01's table; `set_visible` is already listed for PRD 06), so
*adding* it later would be free — which is why "v2" was tempting. *Removing* it
after release is a breaking change: a deprecation cycle of two minor releases
under PRD 01 §5.5's stability promise, a `DeprecationWarning` on first
`set_visible`, a migration note in `CHANGELOG.md`, and the loss of any
encapsulation guarantee an embedder had started relying on. The estimate is
~10% of a release cycle in maintenance plus documentation churn — small, but
strictly worse than shipping it correctly once, because the asymmetry is
unfavourable: adding later costs nothing, removing later costs a cycle. Ship it.

**What would have changed the answer.** If the primitives had required the
graph (i.e. if `set_visible` could only be called with a graph-derived set),
the decision would have been "v2 at the earliest", because the graph is a
non-goal. They do not: `set_visible(M, {tokens})` is a plain dictionary write,
and the framework's own call site (§1.1) is a three-line loop that any embedder
can reproduce.

### 5.2 Vocabulary and data model

| Term | Meaning |
|------|---------|
| **Owner** | The module class passed as `owning_module=` at registration. `None` means *global* |
| **Row** | One `Provider` entry in the container. A token may map to several rows (`multi=True`, PRD 04) |
| **Domain** | The set of rows a resolution call may use. A domain is either a module class or `GLOBAL` (`owning_module=None`) |
| **Grant** | One entry in a visible set: a bare token, or a `(token, owner)` pair naming a single row |
| **Visible set** | The grants installed for one module, plus that module's own providers |
| **Global view** | The domain used when a resolution passes `owning_module=None` while encapsulation is on |
| **Encapsulation** | The container-wide mode that switches on after the first `set_visible` / `grant_visible` call |

Two public type aliases make signatures readable and are exported for typing
users (PRD 01 §5.3.2):

```python fragment
from typing import Any, TypeAlias

#: One entry in a visible set: a token (all rows bound to it) or a row address.
Grant: TypeAlias = Any | tuple[Any, type]

#: The module class that declared a provider, or None for a global provider.
Owner: TypeAlias = type | None
```

`Grant` is deliberately structural rather than a class: a grant is either a
provider token (already hashable, PRD 04) or a two-tuple. There is no
`Grant(...)` constructor to learn, and `frozenset({Sender})` keeps working
exactly as it does in the reference.

### 5.3 `owning_module` on the provider record

`Provider` gains nothing it does not already have in PRD 01's field list; this
section fixes its *meaning*:

| Field | Type | Meaning |
|-------|------|---------|
| `owning_module` | `type \| None` | The class passed at registration. `None` = global (visible in every domain) |

Rules:

- **MV-01.** `Provider.owning_module` defaults to `None`. A provider registered
  without the keyword is global and stays visible in every domain, including
  after encapsulation is switched on (`probe E-204`, T06-05).
- **MV-02.** Every registration entry point accepts `owning_module`:
  `register`, `register_value`, `register_instance`, `register_class`,
  `register_factory`, `register_alias`, `register_custom`. The reference threads
  it through all seven (`_di/__init__.py:331, 631, 691, 744, 806, 866, 899`);
  `lauren-di` keeps the parity.
- **MV-03.** The value must be a class. A non-class (an instance, a string, a
  `Token`) raises `DecoratorUsageError` at registration with
  `detail={"token": ..., "owning_module": repr(value)}`. Validating here turns a
  later, inexplicable "invisible from module <object at 0x…>" into a
  registration error.
- **MV-04.** The owner does **not** create a namespace: two rows for one token
  are a duplicate-binding conflict under PRD 04's rules even if their owners
  differ, unless both are `multi=True`. Visibility filters rows; it never
  disambiguates them.
- **MV-05.** `owning_module` is part of the immutable record returned to callers;
  it is readable on every row from `get_provider`, `visible_in` and `explain`.

### 5.4 Declaring a visible set

```python fragment
class DIContainer:
    def set_visible(
        self,
        module_cls: type,
        grants: frozenset[Grant],
    ) -> None: ...

    def grant_visible(
        self,
        module_cls: type,
        grants: frozenset[Grant],
    ) -> None: ...

    def visible_in(self, module_cls: type) -> frozenset[Any]: ...
```

| Method | Semantics |
|--------|-----------|
| `set_visible(M, grants)` | **Replaces** the declared grants for `M` with `grants`. The effective visible set is `own(M) ∪ grants` (MV-06) |
| `grant_visible(M, grants)` | **Unions** `grants` into the existing effective set. If `M` has no declaration, it seeds with `own(M)` then unions |
| `visible_in(M)` | Returns the *effective* visible set as a frozenset of tokens (own providers first, then grants). Rows addressed by `(token, owner)` appear as their bare token |

```python fragment
class BillingModule: ...
class ReportingModule: ...

container.register(InvoiceService, owning_module=BillingModule)
container.register(ReportService, owning_module=ReportingModule)

# Billing sees its own provider, and may read reports. Nothing else.
container.set_visible(BillingModule, frozenset({ReportService}))

# Reporting sees its own provider only.
container.set_visible(ReportingModule, frozenset())

async def main() -> None:
    await container.resolve(InvoiceService, owning_module=BillingModule)   # ok
    await container.resolve(ReportService, owning_module=BillingModule)    # ok (granted)
    await container.resolve(InvoiceService, owning_module=ReportingModule) # MissingProviderError
```

Rules:

- **MV-06.** `own(M)` — the rows whose `owning_module is M` — are **implicitly
  visible** to `M` and cannot be revoked by omitting them from a grant. This is
  the one place the design deliberately diverges from the reference, where
  `set_visible(M, frozenset())` hides the module's own providers
  (`probe E-209`). The framework never notices the difference because it always
  passes `visible_in(M)`, which already contains `own(M)` (`probe E-218`).
- **MV-07.** A bare token grant covers **every row bound to that token**,
  including rows created by `provides=[...]` on a concrete class
  (PRD 04 §5.7). This fixes `probe E-215`, where granting the protocol token
  failed to expose the provider registered under it.
- **MV-08.** A `(token, owner)` grant covers **one row**: the row for `token`
  whose `owning_module is owner`. It is how a domain grants one `multi` sibling
  without granting its siblings (§5.7).
- **MV-09.** A grant that matches no row is **inert, not an error**: it is
  recorded and becomes effective if a matching row is registered later.
  Declaration order must not matter (MV-30), and a grant naming a token that has
  not been registered yet is a normal way to write a module table before
  populating the container.
- **MV-10.** `set_visible(M, frozenset())` is legal and means "own providers
  only". So is `grant_visible` on a module that owns nothing — a pure consumer
  module.
- **MV-11.** Grants are keyed by module class identity, not by name: two modules
  with the same `__name__` in different scopes are different domains
  (`probe E-218`'s `visible_in` is keyed by class).
- **MV-12.** Visibility state is per container instance. Two containers never
  share grants, even when they receive the same module classes.
- **MV-13.** Declaring is the switch: the first successful `set_visible` or
  `grant_visible` call turns encapsulation on for that container, and it stays
  on for the container's lifetime (there is no "off" call). Removing the
  declaration path would otherwise leave a half-scoped container whose behaviour
  depended on call order.

### 5.5 Resolution rules

Every resolution entry point — `resolve`, `get_provider`, `has_provider`, and
`ResolutionScope.resolve` (PRD 03 §5.3) — accepts the same keyword and applies
the same filter:

| # | Row owner | Call's `owning_module` | Encapsulation | Outcome |
|---|-----------|------------------------|---------------|---------|
| 1 | `None` (global) | anything, including `None` | off or on | **visible** (MV-01) |
| 2 | `M` | `M` | off or on | **visible** (MV-06) |
| 3 | `N` (≠ `M`) | `M`, bare-token grant on that token | on | **visible** (MV-07) |
| 4 | `N` | `M`, `(token, N)` grant | on | **visible** (MV-08) |
| 5 | `N` | `M`, no matching grant | on | `MissingProviderError`, `reason="not_visible"` |
| 6 | any | `M` with no declaration | on | `MissingProviderError`, `reason="undeclared_module"` |
| 7 | `M` or `N` | `None` | on | **visible only if the row is global**: module-owned rows are invisible in the global view (MV-23) |
| 8 | anything | `None` | off | **visible**: the flat registry (PRD 05 §5.1) |

Rules:

- **MV-14.** With encapsulation on, `owning_module=None` on a *resolution* means
  the **global view**: only rows whose `owning_module is None`. It does not mean
  "all domains". This is what makes the ambiguity rule of MV-16 well-founded and
  what stops an unscoped call from becoming a universal bypass
  (`probe E-201` measures the reference's opposite choice; §6.3 records the
  divergence).
- **MV-15.** `has_provider(T, owning_module=M)` is exactly "would a resolution
  of `T` from `M` find at least one visible row", ambiguity aside
  (`probe E-205`: `False` for a foreign token, `True` for an own one). It never
  returns `True` for a row the resolution would refuse.
- **MV-16.** An invisible token raises `MissingProviderError`, never a silent
  `None` and never "not registered": `detail` carries `token`, `module`,
  `reason="not_visible"` and a `hint` naming the declaring module
  (`probe E-202`: the reference's message and keys, minus `reason`).
- **MV-17.** An undeclared module raises `MissingProviderError` with
  `reason="undeclared_module"` and a `hint` of the form
  `"call set_visible({module.__name__}, ...) to declare what it may see"`.
  Failing closed is the fix for `probe E-203`.
- **MV-18.** A consumer with `owning_module` set to a non-class value cannot
  occur: the keyword is validated on the way in (MV-03) for registration, and on
  the way in for resolution too (same error).
- **MV-19.** The filter is applied **before** ambiguity is judged, **before**
  scope narrowing is checked, and **before** a cycle is followed, so an
  invisible row never contributes a phantom ambiguity, a phantom scope
  violation or a phantom cycle edge.
- **MV-20.** Resolution never widens transitively. An own provider of `M` that
  depends on a row not visible to `M` fails at `M`'s domain, not at the
  dependent's owner, even when the dependent's owner could see it
  (`probe E-206` for the compile-time case).

### 5.6 Modes, the global view, and what compile checks

Two modes, one switch, no third state:

```text
   no declaration                      first set_visible / grant_visible
  ─────────────────►  OFF  ──────────────────────────────────────────►  ON
                     flat registry                                   every module-scoped call is filtered
```

- **MV-21.** With encapsulation **off**, `owning_module` is recorded on the
  provider and ignored by resolution: every call sees every row, exactly as in
  PRDs 02–05. This is what the reference does when `_visible` is empty
  (`_is_visible` returns `True` unconditionally), and it is what keeps the
  decision's blast radius at zero (T06-21).
- **MV-22.** With encapsulation **on**, the filter applies to *every*
  module-scoped call, including calls for modules that were never declared.
  There is no per-module opt-in: one declaration is a statement about the whole
  container. The alternative — per-module strictness with a flat default — is
  the fail-open defect of §1.2 (T06-22).
- **MV-23.** The **global view** is the domain used when a resolution passes
  `owning_module=None` while encapsulation is on. It contains exactly the rows
  whose `owning_module is None`. Global rows are therefore the only ones any
  unscoped call can reach, in every domain, in both modes (T06-23).
- **MV-24.** Compile-time ambiguity is evaluated **per domain**. A token with
  `k > 1` non-`multi` rows is an error only if some domain can see two of them.
  The domains are each declared module plus the global view. Two modules that
  each bind one implementation of a Protocol are therefore legal
  (`probe E-207` is the reference's failure; T06-24 is the fix).

  Cost is bounded by bucketing: group rows by token, then by owner, and check
  `buckets(token, M) + globals(token) > 1` for each declared `M`. That is
  `O(rows + declared modules)` per token, not `O(rows × modules)`.

  Consequence, stated plainly: with encapsulation on, the *same* token may
  legitimately mean different things in different domains, and an unscoped
  `resolve(T)` sees only global rows (MV-23), so it cannot observe the clash.
  With encapsulation off there is one domain, the flat registry, and the rule
  degenerates to PRD 05's unconditional sweep — no behaviour change for existing
  users.
- **MV-25.** Every compile phase from PRD 05 §5.2 evaluates an edge **in the
  domain of the row that declares the edge**: provider `p` with
  `p.owning_module = M` resolves its dependencies as `M`. This is already true
  in the reference (`compile` passes `owning_module=provider.owning_module` into
  `resolve_one`, `_di/__init__.py:1180–1186`) and `probe E-206` shows it failing
  fast: an invisible dependency edge raises `MissingProviderError` during
  `compile()`, and `_compiled` stays `False` (T06-25).
- **MV-26.** `set_visible` and `grant_visible` **invalidate** compilation,
  exactly like `register*` (PRD 05 §5.1). The next `compile()` revalidates
  against the new domain; until then `_compiled` is `False` and the next
  `resolve` triggers compilation. This closes the stale-validation window of
  `probe E-225`, where a post-compile widening took effect with no validation
  (T06-26).

Worked example — the pathological case, resolved:

```python fragment
from lauren_di import DIContainer, injectable


class BillingModule: ...
class ShippingModule: ...


class Store:  # a structural Protocol works the same way
    def total(self) -> int: ...


@injectable(provides=[Store])
class BillingStore(Store):
    def total(self) -> int:
        return 100


@injectable(provides=[Store])
class ShippingStore(Store):
    def total(self) -> int:
        return 5


container = DIContainer()
container.register(BillingStore, owning_module=BillingModule)
container.register(ShippingStore, owning_module=ShippingModule)
container.set_visible(BillingModule, frozenset())
container.set_visible(ShippingModule, frozenset())

# Legal: no domain sees both rows.
container.compile()

async def totals() -> tuple[int, int]:
    billing = await container.resolve(Store, owning_module=BillingModule)
    shipping = await container.resolve(Store, owning_module=ShippingModule)
    return billing.total(), shipping.total()
```

### 5.7 Collections, aliases, and structural protocols

- **MV-27.** `list[T]` resolution filters rows per domain **before** building
  the list (PRD 04 §5.8, PRD 05 row 16). A module sees its own `multi` rows plus
  granted ones — never a sibling owned by another module. `probe E-216` shows the
  reference reaching the same outcome when the caller names concrete classes;
  MV-07 makes it work when the caller names the token (T06-27).
- **MV-28.** If a collection filters down to zero visible rows, the error is
  `MissingProviderError` with `token` (the rendered `list[T]`), `element`,
  `module`, `reason="not_visible"` and the hint to grant the element. It is not
  an empty list: an empty list would make a missing grant indistinguishable from
  a legitimately empty collection, and `multi=True` cannot express "empty on
  purpose" (T06-28).
- **MV-29.** An alias row (`use_existing`, PRD 04 §5.5) never widens visibility.
  Resolution of the alias is filtered by the alias row's own domain; the alias's
  target edge is then resolved **in that same domain**, not in the target's
  owner's domain. An alias in `M` pointing at a row `M` cannot see raises
  `MissingProviderError` for the target, with `alias` and `module` in `detail`.
  Without this rule an alias is a one-line bypass around the whole feature
  (T06-29).
- **MV-30.** The structural-Protocol scan (PRD 04 §5.6) runs over the domain's
  visible rows only. `probe E-208` measures the reference doing this correctly
  for two modules with their own runners (T06-30).
- **MV-31.** The scan never matches the protocol class itself, only proper
  structural subtypes — the reference's `p.cls is not protocol` guard
  (`_di/__init__.py:2233`). A protocol that is also registered as a concrete
  class does not shadow its implementers (T06-31).
- **MV-32.** When a domain has more than one structural match, the scan raises
  `ProtocolAmbiguityError` carrying `token`, `count` and `modules` (the sorted
  owning-module names). The reference raises the same class with an **empty**
  `detail` on this path (`probe E-208c`: `detail {}`), which leaves the caller
  with a message and nothing else (T06-32). In the global view the scan covers
  global rows only (MV-23).

### 5.8 Diagnostics

```python fragment
class DIContainer:
    def explain(
        self,
        token: Any,
        *,
        owning_module: type | None = None,
    ) -> str: ...
```

`explain` answers the only question a visibility error leaves open: *who has it,
and why can I not see it?* It is a pure read: it never mutates, never compiles,
and never raises.

```python fragment
report = container.explain(InvoiceService, owning_module=ReportingModule)
```

```text
token: InvoiceService
domain: ReportingModule (declared)
row 1: owner=BillingModule visible=no remedy="grant InvoiceService to ReportingModule"
```

- **MV-33.** For a visible token the report is a stable, deterministic string
  listing the chosen row first, with `token:` and `domain:` header lines
  (T06-33).
- **MV-34.** For an unknown token the report says `row: none` and
  `status: unregistered`; `explain` never raises, so it is safe in a
  `except DIError:` handler and in a log line (T06-34).
- **MV-35.** Every row that binds the token appears, in registration order, with
  `owner=`, `visible=` and a per-row `remedy=` when invisible (T06-35).
- **MV-36.** Field labels are part of the contract: `token:`, `domain:`,
  `row`, `status:`, `owner=`, `visible=`, `remedy=`. Tests assert by substring,
  not by whole-string equality, so extra whitespace may change without breaking
  callers (T06-36).

### 5.9 The rejected branch: an inert seam

For the record — and so a later reversal has a specification to revert to — this
is what D-5's "inert in v1" branch would have said. It is **not** what this PRD
chooses (D-37).

| Aspect | Inert-seam contract (rejected) |
|--------|-------------------------------|
| `owning_module=` | Accepted on all nine methods, stored on `Provider`, never consulted |
| `set_visible` | Exists, warns `DeprecationWarning` once per container, does nothing |
| `resolve(..., owning_module=M)` | Accepted, ignored; always the flat registry |
| Errors | None: nothing can fail, because nothing is enforced |
| Migration risk | Highest of the three options: parity tests must assert *different* behaviour between `lauren` and `lauren_di` for the same call |
| Removal cost | Zero — but so is the value |

- **MV-37.** If this branch is ever reinstated, the compatibility guarantee of
  §5.11 continues to apply unchanged, and `set_visible` must keep accepting the
  same signature so that code written against v1 keeps importing and calling it.

### 5.10 If the graph ships in v2: the full design

The decision is that it does not (D-37), and that v2 does not change it either.
Recording the design anyway is what makes the decision *reviewable*: a reader can
see exactly what was declined and what it would cost.

**What the reference has** (measured):

| Piece | Behaviour | Probe |
|-------|-----------|-------|
| `@module(providers=, imports=, exports=)` | `ModuleMeta` carries four tuples plus framework-only `controllers`, `host`, `name` (`decorators.py:146`) | — |
| `ModuleGraph.compile(root)` | DFS with cycle detection; imported modules visited first so their exports are known (`_modules/__init__.py:199`) | E-218 |
| `visible_in(M)` | `own(M) ∪ exported-by-imports`, controllers included for `M` itself, never exported | E-218 |
| Transitive re-export | *Not* automatic: `Top → Middle → Deep` sees `Deep`'s exports only if `Middle` re-exports them | E-224 |
| Export validation | `ModuleExportViolation {module, export}` when a module exports what it neither declares nor imports | E-219 |
| Single-owner validation | `ModuleExportViolation {provider, modules: [...]}` when two modules declare the same provider — same class, different keys | E-220 |
| Cycle | `CircularModuleError {cycle: [...]}`, names only | E-222 |
| Undecorated subclass | `MetadataInheritanceError {class, inherits_from}` — different keys from PRD 05 row 25's `target`/`base` | E-223 |
| Unknown module | `visible_in(Unknown)` returns `frozenset()` | E-218 |
| Substitution | `compile(substitutions=...)` replaces a module wherever imported | — |

**What it would take here:** a `_graph.py` with `ModuleMeta`, a decorator, a DAG
walk, forward-reference resolution, and three exception classes that this PRD set
has just finished keeping out of the library. The adapter to v1 is three lines —
*that* is the important part:

```python fragment
for module_cls in graph.modules:
    container.set_visible(module_cls, graph.visible_in(module_cls))
```

- **MV-38.** If a graph is ever added, it lives outside `lauren_di` (an optional
  package) and **feeds** `set_visible`. `lauren_di` never imports it, and its
  presence must not change any signature in this PRD (T06-38).
- **MV-39.** No graph may change the resolution rules of §5.5–§5.7. A graph only
  *computes* grants; the domain model, the error shapes and the fail-closed rule
  stay exactly as specified here. A future graph that needs different semantics
  is a different feature and needs its own decision (T06-39).

Why the graph stays out permanently: `ModuleMeta` carries `controllers`, `host`
and `name` — routing and multi-tenancy concerns (PRD 01 §3). Stripping them
leaves `providers`/`imports`/`exports`, i.e. a declarative way to compute the
sets that `set_visible` already accepts. That is convenience, not capability, and
it costs three exception classes, a decorator, a DAG walk and a substitution
hook. The cost/benefit is the mirror image of the one that justified shipping the
primitives: here the capability already exists through a three-line loop.

### 5.11 The compatibility guarantee

Five promises, each testable:

1. **Additive at the API level.** Every signature in PRD 01 §5.3 keeps its
   meaning; `owning_module` and `set_visible` were already listed there as
   PRD 06's, so no v1.0 caller sees a signature they did not have.
2. **No declaration, no change.** A container that never calls `set_visible` or
   `grant_visible` resolves exactly as PRDs 02–05 specify, in every 1.x release
   (MV-21).
3. **No silent widening.** Within 1.x, a row that is invisible in a domain may
   not become visible without a new grant for that domain. Narrowing is likewise
   a breaking change: both directions alter an access decision, so both require
   a major (MV-40).
4. **Global stays global.** A provider registered with `owning_module=None`
   remains visible in every domain, including the global view, in every 1.x
   release.
5. **The graph, if it ever appears, is additive.** It would compute grants and
   call `set_visible`; no existing call may change meaning because a graph
   exists in the process (MV-38, MV-39).

- **MV-40.** Removal or semantic change of any rule in §5.5 requires a major
  version: a deprecation cycle of two minor releases, a `DeprecationWarning` on
  first use, a `CHANGELOG.md` entry under "Changed", and a migration paragraph in
  `docs/guides/migration.md`. This is the reversibility cost quantified in §5.1
  and the reason the decision is made once, here, rather than left open.

---

## 6. Architecture Fit & Feasibility

### 6.1 Extraction map

Every behaviour in §5 exists in the reference; the column "library rule" says
what survives the move.

| Reference (`../lauren-framework`) | Symbol / line | Behaviour | Library rule |
|---|---|---|---|
| `lauren/_di/__init__.py` | `Provider.owning_module` 223–226 | Field recording the declaring module, `None` = global | MV-01, MV-05 |
| " | `register(..., owning_module=)` 331 | Threads the owner into the row | MV-02 |
| " | `register_value` 631, `register_instance` 691, `register_class` 744, `register_factory` 806, `register_alias` 866, `register_custom` 899 | Same keyword on every entry point | MV-02 |
| " | `_visible` dict 300–305 | `module_cls -> frozenset` of visible tokens | MV-12, §5.2 |
| " | `set_visible` 952–960 | Installs the visible set for one module | MV-06 … MV-11 |
| " | `_is_visible` 962–975 | Three predicates: no domain → visible; `cls in visible` → visible; `owning_module is None` → visible | MV-06, MV-07, MV-14 (reworked) |
| " | `resolve_one` list path 1028–1075 | Filters `list[T]` rows by visibility, then counts | MV-27, MV-28 |
| " | `resolve_one` scalar path 1091–1112 | Filters bindings, raises "visible from module" | MV-16, MV-19 |
| " | `compile` edge walk 1180–1186 | Resolves each dependency in the provider's own domain | MV-25 |
| " | `compile` eager sweep 1193–1205 | Flat ambiguity check, visibility-blind | MV-24 (replaced) |
| " | `has_provider` 1213, `get_provider` 1238 | Visibility-aware lookups | MV-15 |
| " | `_structural_protocol_providers` 2208–2237 | Structural scan filtered by `is_visible`, protocol excluded | MV-30, MV-31 |
| `lauren/_modules/__init__.py` | `ModuleGraph.compile` 199, `visible_in` 345 | Derives the sets from `imports`/`exports` | Not carried (D-37); §5.10 |
| `lauren/_asgi/__init__.py` | `set_visible` loop 4128 | Framework wires the graph into the container | The embedder's three-line loop (§5.10) |
| `lauren/_diagnostics.py` | `_missing_provider` 67 | Explains an invisible token using declaring modules | MV-33 … MV-36 (`explain`) |
| `lauren/exceptions.py` | `ModuleExportViolation`, `CircularModuleError` | Graph validation | Not carried (§7) |

### 6.2 Parity table — 40 rules, 40 tests

| Rule | Statement | Test |
|------|-----------|------|
| MV-01 | `owning_module` defaults to `None`; global rows are visible everywhere | T06-01 |
| MV-02 | All seven registration entry points accept `owning_module` | T06-02 |
| MV-03 | A non-class owner is rejected at registration | T06-03 |
| MV-04 | The owner does not namespace tokens; duplicates still conflict | T06-04 |
| MV-05 | `owning_module` is readable and immutable on the row | T06-05 |
| MV-06 | A module's own rows are implicitly visible and cannot be revoked | T06-06 |
| MV-07 | A bare-token grant covers every row bound to that token | T06-07 |
| MV-08 | A `(token, owner)` grant covers exactly one row | T06-08 |
| MV-09 | A grant matching no row is inert, not an error | T06-09 |
| MV-10 | Empty grants are legal; `grant_visible` works on consumer-only modules | T06-10 |
| MV-11 | Domains are keyed by class identity, not by name | T06-11 |
| MV-12 | Visibility state is per container | T06-12 |
| MV-13 | The first declaration switches encapsulation on for the container | T06-13 |
| MV-14 | With encapsulation on, `owning_module=None` means the global view | T06-14 |
| MV-15 | `has_provider` agrees with `resolve`'s filter | T06-15 |
| MV-16 | An invisible token raises `MissingProviderError` with `module` and `reason` | T06-16 |
| MV-17 | An undeclared module fails closed with a `set_visible` hint | T06-17 |
| MV-18 | A non-class consumer is rejected on resolution too | T06-18 |
| MV-19 | The filter runs before ambiguity, scope and cycle checks | T06-19 |
| MV-20 | Resolution never widens transitively | T06-20 |
| MV-21 | With encapsulation off, resolution is the flat registry | T06-21 |
| MV-22 | Enabling encapsulation is container-wide | T06-22 |
| MV-23 | The global view contains exactly the global rows | T06-23 |
| MV-24 | Ambiguity is judged per domain, not over the flat table | T06-24 |
| MV-25 | Compile evaluates each edge in its declaring row's domain | T06-25 |
| MV-26 | Visibility declarations invalidate compilation | T06-26 |
| MV-27 | `list[T]` keeps only rows visible to the domain | T06-27 |
| MV-28 | A collection filtered to zero rows raises, with `element` in `detail` | T06-28 |
| MV-29 | An alias never grants visibility to its target | T06-29 |
| MV-30 | Structural scanning is domain-scoped | T06-30 |
| MV-31 | The protocol class itself is never a structural match | T06-31 |
| MV-32 | Multi-match structural ambiguity carries `token`, `count`, `modules` | T06-32 |
| MV-33 | `explain` returns a stable report for a visible token | T06-33 |
| MV-34 | `explain` never raises, including for unknown tokens | T06-34 |
| MV-35 | `explain` lists every row with owner and verdict | T06-35 |
| MV-36 | `explain` field labels are part of the contract | T06-36 |
| MV-37 | The rejected inert branch keeps the same signature | T06-37 |
| MV-38 | A future graph feeds `set_visible` and is never imported here | T06-38 |
| MV-39 | A future graph may not change §5.5–§5.7 | T06-39 |
| MV-40 | The compatibility guarantee is enforced by test | T06-40 |

### 6.3 Divergences

| ID | Divergence | Reference behaviour (probe) | Reason |
|----|-----------|-----------------------------|--------|
| D-37 | Decision: primitives ship **active in v1**; graph out of scope for v1 *and* v2 | D-5 left the question open; the seam existed but only a graph ever fed it | An inert parameter silently changes semantics for migrators (§1.1); the primitives cost ~150 lines and are opt-in |
| D-38 | `owning_module=None` on **resolve** means the *global view* once encapsulation is on | Flat fallback: unscoped resolution sees module-owned rows (`probe E-201`) | The fallback is a universal bypass; making it a defined, narrow domain keeps MV-24 decidable and fail-closed |
| D-39 | Encapsulation is **container-wide and fail-closed**: a module with no declaration raises | Per-module fail-open: undeclared modules see everything (`probe E-203`); `set_visible(M, ∅)` hides `M`'s own rows (`probe E-209`) | Both behaviours are silent. A missing declaration is a wiring bug and must be loud (MV-17, MV-22) |
| D-40 | Ambiguity is evaluated **per domain** | Flat sweep rejects two modules that each bind one implementation (`probe E-207`) | The rejected pattern *is* the encapsulation use case; the sweep validated a graph no caller sees |
| D-41 | Grants are **row-addressable**; a token grant covers `provides=[...]` rows; own rows are implicit | `provider.cls in visible` misses `provides` rows (`probe E-215`) and cannot address one `multi` sibling (`probe E-217`) | Visibility should answer "may this domain use this token", not "is this class name listed" |
| D-42 | Structural ambiguity carries `token`, `count`, `modules` | Empty `detail` (`probe E-208c`) | An error without the offending token is not actionable; PRD 05 §5.7 requires every catalogue row to carry keys |
| D-43 | `set_visible` / `grant_visible` **invalidate** `compile()` | Mutates live behaviour without clearing `_compiled` (`probe E-225`) | Validation must never be stale (PRD 05 §5.1); the window allows unvalidated widening |

Every divergence is additive from a caller's point of view: code written against
the reference's *successful* paths keeps working, because those paths are the ones
this design reproduces. The differences appear only where the reference silently
succeeds when it should not, or fails when it should not.

### 6.4 What visibility never does

The list is as load-bearing as the rules, because each item is a plausible
extension that would quietly turn this into a module framework:

- **It never constructs a module.** There is no module object, no lifecycle, no
  state attached to a module class. A module is a key in a dictionary.
- **It never scans or discovers.** No import of the caller's package, no
  `__subclasses__`, no entry points. Grants come from calls.
- **It never derives.** No function computes a visible set from another visible
  set. §5.10's loop is the only derivation, and it lives outside the library.
- **It never caches per module.** The filter runs against the same singleton and
  transient caches as PRD 03; only *reachability* changes, never *lifetime*.
- **It never affects teardown.** A row hidden from `M` may still be owned by `M`
  and is still torn down with `M`'s scope, because ownership is about
  declaration, not access (PRD 03 §5.8).
- **It never changes construction order.** Hooks fire per PRD 03; visibility
  only removes edges from consideration, and a removed edge is one that would
  have raised anyway.
- **It never inspects `__name__`, `__module__` or `__qualname__`.** Class
  identity only (MV-11).
- **It never decrypts a token.** Tokens are compared by identity/equality as
  PRD 04 defines; visibility adds no new token semantics.

### 6.5 Cost

| Operation | Cost | Bound |
|-----------|------|-------|
| `set_visible` / `grant_visible` | One dict write plus an invalidation flag | `O(|grants|)` |
| Resolve, encapsulation off | One boolean check before the existing path | `O(1)` |
| Resolve, encapsulation on | One set membership test per candidate row | `O(rows for the token)` |
| Compile, ambiguity | Bucket rows by token, then by owner | `O(rows + declared modules)` |
| Compile, edge walk | Unchanged asymptotically; each edge filters its own domain | `O(P + E)` as PRD 05 §6.5 |
| Memory | One `dict[type, frozenset[Grant]]` | `O(declared modules + grants)` |
| `explain` | Linear in the rows that bind the token | `O(rows for the token)` |

No new allocation on the hot path when encapsulation is off — which is the
default for every existing user. This is the concrete reason the decision is
cheap: the cost was already being paid by the reference on every resolution,
because `_is_visible` is called there unconditionally.

### 6.6 Evidence index

Every `probe E-2xx` cited above, and the script it comes from:

| Script | Ids emitted | What they gate |
|--------|-------------|----------------|
| `m1.py` | `E-201`, `E-202`, `E-202b`, `E-203`, `E-204`, `E-204b`, `E-205`, `E-205b`, `E-206` | D-38 (global view), fail-closed (§1.2), MV-15, MV-20, MV-23 |
| `m2.py` | `E-207`, `E-207b`, `E-207c`, `E-208`, `E-208b`, `E-208c`, `E-209` | D-40 (per-domain ambiguity), MV-30 ¶MV-32, MV-07 (§1.4) |
| `m3.py` | `E-210`, `E-210b`, `E-210c`, `E-211`, `E-212`, `E-213`, `E-214` | per-token multi visibility, grants to an unregistered module, unowned rows, the empty-grant structural scan |
| `m4.py` | `E-215`, `E-215b`, `E-216`, `E-216b`, `E-216c`, `E-217` | D-41 (row-addressable grants), the `provides=[...]` defect, MV-27 |
| `m5.py` | `E-218`, `E-219`, `E-220`, `E-221`, `E-222`, `E-223`, `E-224` | §6.1's extraction map; the four framework-only graph errors |
| `m6.py` | `E-225`, `E-225b` | D-43 (post-`compile()` widening), MV-26 |

**No reserved ids.** The range `E-201` … `E-225` is contiguous *and* fully
observed: every id in it is emitted by one of the six scripts above, so every
citation in this document regenerates from a single probe run. An earlier draft
listed `E-210` … `E-214` as reserved because `m3.py` halted before its first
probe: the reference's `DIContainer.register` accepts only
`(target, *, owning_module=None)` — neither `multi=` nor `provides=` — so the
multi rows it needs must be built through the `CustomProvider` path
(`use_class(provide=..., use=..., multi=True)`), exactly as `m4.py` does.
`m3.py` now takes that path and the five ids carry their intended observations.

`E-221` is a pointer rather than a measurement: it asserts that the lazy
circular reference *builds*, then defers the raise to `E-222`, whose output
carries `{'cycle': ['CircA', 'CircB', 'CircA']}`. Both are emitted, so a reader
who greps either id lands in `m5.py`'s transcript.

Every transcript, the resolved `lauren.__file__` and the `_di/__init__.py`
checksum are recorded under `prds/evidence/output/` by
`python3 prds/evidence/run_all.py`, which also fails when a cited id is not
emitted by any probe.

---

## 7. Error Catalog Impact

**No new exception class.** Three existing leaves gain triggers and `detail`
keys; PRD 05 §5.6.2's catalogue is extended from 29 rows to 32 by §7.1.

### 7.1 Rows added to PRD 05's catalogue

| # | Leaf | `code` | Trigger | Raised by | `detail` | Message shape | Remediation | Test |
|---|------|--------|---------|-----------|----------|---------------|-------------|------|
| 30 | `MissingProviderError` | `missing_provider` | token exists but is not visible from the consumer's domain | `compile`, `resolve`, `get_provider`, `has_provider` is `False` | `token`, `module`, `reason="not_visible"`, `hint` | `No provider for {token} visible from module {module}` | grant the token to the module, or resolve through the declaring module | T06-16 |
| 31 | `MissingProviderError` | `missing_provider` | consumer module has no visibility declaration while encapsulation is on | `compile`, `resolve` | `token`, `module`, `reason="undeclared_module"`, `hint` | `module {module} has no declared visibility` | call `set_visible({module}, ...)` (or `grant_visible`) before resolving | T06-17 |
| 32 | `DecoratorUsageError` | `decorator_usage` | `owning_module` is not a class | every `register*`, `use_*`, `resolve` | `token`, `owning_module` | `owning_module must be a class, got {owning_module}` | pass the module class itself, or omit the keyword | T06-03 |

### 7.2 Rows in PRD 05 amended

| PRD 05 row | Amendment |
|------------|-----------|
| 8 — `MissingProviderError`, "an edge has no provider" | When encapsulation is on, `detail` also carries `module`; the message appends `visible from module {module}` (`probe E-206`) |
| 9 — `MissingProviderError`, `list[T]` with no element provider | Same `module` addition; `token` renders as `list[T]`, never as bare `list` (PRD 05 D-33; `probe E-215b` shows the reference getting this wrong) |
| 11 — `MissingProviderError`, lookup of an unregistered token | Unchanged when the token is truly unregistered. The invisible case is a *different* message and row 30, so `"No provider for T"` keeps meaning "nothing binds `T`" |
| 13/14 — `ProtocolAmbiguityError` from the sweep and from an edge | `count` is the count **within the offending domain**, and `detail` gains `modules` (the owners of the clashing rows, sorted) |
| 16 — `ProtocolAmbiguityError`, `list[T]` member not `multi` | `count` counts visible rows only (`probe E-216`'s per-module lists) |
| 20 — `DIScopeViolationError` | Unchanged. Visibility filters which edge exists; scope narrowing then judges the edges that remain (MV-19) |

### 7.3 Deliberately not added

| Class | Why not |
|-------|---------|
| `ModuleExportViolation` | There is no export declaration to violate (D-37). `probe E-219` and `probe E-220` show it serving two different triggers with different `detail` keys — a graph-only shape |
| `CircularModuleError` | No module graph, so no module cycles. Provider cycles stay `CircularDependencyError` (`probe E-222` is the graph's version, not this library's) |
| A `VisibilityError` leaf | A new class would force `except` clauses to learn a name for something `MissingProviderError` already expresses, and `reason` distinguishes the two new triggers |

---

## 8. Phased Implementation Plan

The `Provider` field exists from the first release because PRD 02's record
specifies it; the *behaviour* lands in phase 4 of PRD 01 §8 (`0.3.0`), where
"visibility" stops being a possible deferral and becomes an item.

| Step | Version | Contents | Exit criteria |
|------|---------|----------|---------------|
| 1 — Record only | `0.1.0` | `Provider.owning_module`, the keyword on all seven registration methods, validation of the value (MV-01 … MV-05); resolution ignores it | T06-01 … T06-05 green; PRD 02/03 suites unchanged |
| 2 — Domains | `0.3.0` | `set_visible`, `grant_visible`, `visible_in`, the domain resolver, `owning_module` on `resolve`/`get_provider`/`has_provider`/`ResolutionScope.resolve` | T06-06 … T06-18 green; PRD 05 suite still green with encapsulation off |
| 3 — Compile integration | `0.3.0` | Domain-aware ambiguity (MV-24), edge domains (MV-25), invalidation (MV-26) | T06-19 … T06-26 green; `probe E-207`'s graph compiles cleanly |
| 4 — Collections and protocols | `0.3.0` | Filtered `list[T]`, alias containment, structural scan scoping and its ambiguity shape | T06-27 … T06-32 green |
| 5 — Diagnostics | `0.3.x` | `explain()` with the label contract | T06-33 … T06-36 green |
| 6 — Freeze | `1.0.0` | Guarantee statement in the docs, `DeprecationWarning` policy wired to MV-40 | T06-37 … T06-40 green |

Step 1 ships in the first release on purpose: recording the owner from day one
means a later step never has to migrate rows that were registered without it.

---

## 9. Documentation & Convention Updates

| Artefact | Change |
|----------|--------|
| `docs/reference/visibility.md` (new) | `owning_module`, `set_visible`, `grant_visible`, `visible_in`, `explain`, the eight-row state table of §5.5, and the mode switch |
| `docs/guides/modules.md` (new) | One worked example: three modules, one shared provider, one deliberate grant, one failure with the exact message |
| `docs/guides/migration.md` | How `lauren`'s graph call site becomes a three-line loop (§5.10); what `lauren` users lose (controllers in the visible set, derived exports) |
| `docs/reference/errors.md` | Rows 30–32 of §7.1 and the amendments of §7.2 |
| `docs/reference/api.md` | The four new methods and the `Grant` / `Owner` aliases |
| `llms.txt` / `llms-full.txt` (goal #11) | Entries for `owning_module`, `set_visible`, `grant_visible`, `visible_in`, `explain`, `Grant`; the checker must fail if any is dropped |
| `CHANGELOG.md` (goal #11) | `0.1.0` "Added: `owning_module` recorded"; `0.3.0` "Added: module visibility"; the guarantee of §5.11 under "Changed" only when it next changes |
| Convention: docstring style | Every method's docstring states its domain behaviour in one line, because the mode switch makes "visible" ambiguous in prose |
| Convention: examples | Every visibility example must show the failure case too. An example that only shows a successful grant teaches half the feature |
| Convention: no `module` word unqualified | Docs say "domain" for the filter and "module" for the key; PRD 08's glossary records both, because `lauren` uses "module" for the graph |

---

## 10. Test Plan

### 10.1 Enumerated test cases

`T06-nn` asserts `MV-nn`, one-to-one. Fixtures: a fresh `DIContainer` per test;
module classes declared at module scope in the test file so identity is stable
(MV-11).

| Test | Name | Setup | Assertion |
|------|------|-------|-----------|
| T06-01 | `test_global_row_visible_everywhere` | one row registered without owner; two declared domains | both domains resolve it |
| T06-02 | `test_all_entry_points_accept_owner` | parametrised over the seven methods | `get_provider(...).owning_module is Mod` |
| T06-03 | `test_non_class_owner_rejected` | `owning_module=object()` | `DecoratorUsageError`, `detail` has `token` and `owning_module` |
| T06-04 | `test_owner_does_not_namespace` | same token, two owners, both non-`multi` | `DuplicateBindingError` at the second registration |
| T06-05 | `test_owner_is_immutable_on_row` | register, read row | `owning_module is Mod`; assignment raises `FrozenInstanceError` |
| T06-06 | `test_own_rows_implicitly_visible` | `set_visible(Mod, frozenset())` | own provider still resolves in `Mod` |
| T06-07 | `test_token_grant_covers_provides_rows` | `@injectable(provides=[Proto])`; grant `{Proto}` | resolves the concrete row |
| T06-08 | `test_row_grant_is_single_row` | two `multi` rows, grant `{(Token, ModB)}` | only `ModB`'s row is visible |
| T06-09 | `test_unmatched_grant_is_inert` | grant a token registered later | no error; resolution works after registration |
| T06-10 | `test_empty_grants_are_legal` | consumer-only module, empty grant | own rows visible, foreign not; no error |
| T06-11 | `test_domains_keyed_by_identity` | two classes both named `M` | grants do not cross |
| T06-12 | `test_visibility_is_per_container` | two containers, one declaration each | no grant leaks between them |
| T06-13 | `test_first_declaration_enables_mode` | assert mode off, call `set_visible`, assert on | `encapsulation_enabled` flips once |
| T06-14 | `test_none_means_global_view` | module-owned row; `resolve(T, owning_module=None)` | `MissingProviderError` with `reason="not_visible"` |
| T06-15 | `test_has_provider_matches_resolve` | matrix of 4 (owner, grant) pairs | boolean agrees with resolve outcome |
| T06-16 | `test_invisible_token_error_shape` | foreign row, no grant | `detail` keys `token`, `module`, `reason`, `hint`; message names the module |
| T06-17 | `test_undeclared_module_fails_closed` | module never declared | `MissingProviderError`, `reason="undeclared_module"`, hint names `set_visible` |
| T06-18 | `test_non_class_consumer_rejected` | `owning_module="ModA"` on resolve | `DecoratorUsageError` |
| T06-19 | `test_filter_precedes_other_checks` | invisible row that is also request-scoped and cyclic | error is `MissingProviderError`, not scope or cycle |
| T06-20 | `test_no_transitive_widening` | `A` owns `X`; `X` depends on `Y` owned by `B`, not granted | failure reported at `A`, not at `B` |
| T06-21 | `test_mode_off_is_flat_registry` | no declarations; module-owned rows | every row resolves from every domain |
| T06-22 | `test_mode_is_container_wide` | declare one module; call from a second, undeclared | second module raises; not silently flat |
| T06-23 | `test_global_view_contents` | global row + two module rows | global view resolves exactly the global row |
| T06-24 | `test_ambiguity_per_domain` | two modules, one non-`multi` row each | `compile()` succeeds; each domain resolves its own |
| T06-25 | `test_compile_uses_consumer_domain` | edge from `A` to a row not granted to `A` | `compile()` raises `MissingProviderError`; `_compiled` stays `False` |
| T06-26 | `test_declaration_invalidates_compile` | compile, `set_visible`, inspect | `_compiled is False`; next resolve recompiles |
| T06-27 | `test_list_is_domain_filtered` | two `multi` rows in two modules | each domain sees one element |
| T06-28 | `test_empty_collection_raises` | grant excludes the element | `MissingProviderError` with `element`; not `[]` |
| T06-29 | `test_alias_cannot_tunnel` | alias in `A` to row in `B`, not granted | `MissingProviderError` with `alias` and `module` |
| T06-30 | `test_structural_scan_is_domain_scoped` | two runners, two modules | `A` gets `RunnerA`, `B` gets `RunnerB` |
| T06-31 | `test_protocol_itself_not_matched` | protocol also bound as a class row | scan returns implementers, not the protocol row |
| T06-32 | `test_structural_ambiguity_detail` | two matches in one domain | `ProtocolAmbiguityError` with `token`, `count`, `modules` |
| T06-33 | `test_explain_visible_token` | visible row | report starts with `token:` and `domain:`, names the row |
| T06-34 | `test_explain_never_raises` | unknown token, undeclared module, weird token | returns a string; `status: unregistered` |
| T06-35 | `test_explain_lists_rows` | three rows for one token | three `row` lines in registration order with `owner=` |
| T06-36 | `test_explain_labels_are_stable` | any report | contains `token:`, `domain:`, `owner=`, `visible=`, `remedy=` |
| T06-37 | `test_inert_signature_preserved` | introspect `set_visible` | `(self, module_cls, grants)`, same names |
| T06-38 | `test_graph_is_not_imported` | walk `lauren_di.__all__` and the wheel's import graph | no graph symbol; `set_visible` accepts a graph-derived set |
| T06-39 | `test_graph_cannot_change_rules` | adapter loop over a fake graph object | resolution rules of §5.5 hold unchanged |
| T06-40 | `test_compatibility_guarantee` | parameterised over the five promises | each holds in the current release |

### 10.2 Parity tests

The parity suite of PRD 08 runs both libraries behind one fixture. For visibility
the shared invariants are the ones both must satisfy (left), and the ones only
`lauren-di` must satisfy (right, each a divergence of §6.3).

| Invariant | Fixture | Expected in `lauren` | Expected in `lauren_di` |
|-----------|---------|---------------------|--------------------------|
| A granted token resolves | two modules, one grant | instance | instance |
| An ungranted token raises | same, no grant | `MissingProviderError` | `MissingProviderError` |
| A global row resolves everywhere | row with no owner | instance | instance |
| A module's own row resolves in it | own row, empty visible set | instance (framework passes own tokens) | instance (implicit) — D-39 |
| Undeclared module | `set_visible` for `ModA` only, resolve from `ModC` | instance (fail-open) | `MissingProviderError` — D-39 |
| Unscoped resolve of a module row | `resolve(T)` | instance (flat fallback) | `MissingProviderError` — D-38 |
| Two modules, one implementation each | `compile()` | `ProtocolAmbiguityError` | clean — D-40 |
| Grant names the protocol token | `provides=[Proto]`, grant `{Proto}` | `MissingProviderError` | instance — D-41 |
| Structural scan with two matches in one domain | two runners, one module | `ProtocolAmbiguityError`, empty `detail` | same class, `token`/`count`/`modules` — D-42 |
| Widen after compile | `set_visible` post-`compile` | takes effect, no revalidation | invalidates, revalidates — D-43 |

The suite asserts the *left* column as parity and the *right* column as a
recorded divergence, so a regression in either library fails a named test rather
than a snapshot.

### 10.3 Acceptance traceability for this PRD

| Acceptance clause (goal #7) | Where satisfied | Evidence |
|-----------------------------|-----------------|----------|
| Explicit decision record on v1 vs v2 | §5.1, D-37 | One quoted statement; resolves D-5; rationale table and reversibility cost follow it |
| `owning_module` on providers | §5.3, MV-01 … MV-05 | Extraction map cites `_di/__init__.py:223`, 331, 631–899 |
| `set_visible` | §5.4, MV-06 … MV-13 | Cited at `_di/__init__.py:952`; replace semantics and the implicit own-set defined |
| Visibility-filtered resolution | §5.5, MV-14 … MV-20 | Eight-row state table; every outcome names its rule |
| `None` meaning flat registry | §5.6, MV-21, MV-23 | Two modes; the flat registry when nothing is declared, the global view afterwards |
| Structural-Protocol scanning scoped to the consumer's module | §5.7, MV-30 … MV-32 | `probe E-208` for the scoping, `probe E-208c` for the detail defect it fixes |
| Single unambiguous decision statement | §5.1 | D-37 is one sentence of decision plus one of scope, with no conditional |
| Rationale | §1.1 … §1.5, §5.1 | Four measured defects, each with a probe and a reference line |
| Reversibility cost | §5.1, MV-40 | Two-minor deprecation cycle, warning, changelog and migration note, quantified |
| If deferred to v2, seam documented but inert | §5.9, MV-37 | Not the chosen branch (D-37); the inert contract is nevertheless specified row by row |
| Compatibility guarantee | §5.11, MV-40 | Five promises, each with a test (T06-40) |
