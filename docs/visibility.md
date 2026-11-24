# Module visibility

Visibility is **opt-in**. A fresh container is a flat registry: every row is
visible from every domain and `owning_module` is inert metadata. The first call
to `set_visible` or `grant_visible` turns encapsulation on for the whole
container.

## Declaring a row's owner

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, MissingProviderError, injectable


class BillingModule:
    pass


@injectable()
class Store:
    pass


container = DIContainer()
container.register(Store, owning_module=BillingModule)
```

`owning_module=None` (the default) means a **global** row: visible to everyone,
from every domain.

## Declaring what a module may see

<!-- docs-contract: run -->
```python
container.set_visible(BillingModule, frozenset())           # own rows only
container.grant_visible(BillingModule, frozenset({Store}))  # plus Store
```

`set_visible` replaces the granted set; `grant_visible` unions into it. Both
invalidate the compiled graph, so a later `resolve` re-validates. A grant is a
bare token, or a `(token, owner)` pair — `frozenset({(Store, BillingModule)})` —
naming one row when two modules bind the same token.

Two guarantees are worth stating explicitly:

1. **A module always sees its own rows.** `set_visible(M, frozenset())` cannot
   revoke `M`'s own bindings — it only removes *other* modules' rows.
2. **A module must be declared.** Once encapsulation is on, resolving from a
   module that never called `set_visible` fails closed with
   `reason="undeclared_module"`.

## The resolution table

With encapsulation on, `owning_module` selects the view:

| caller | sees |
|---|---|
| `None` | the global view: rows with `owning_module is None` only |
| a declared module `M` | `M`'s own rows + global rows + `M`'s grants |
| an undeclared module | nothing (`reason="undeclared_module"`) |

Global rows stay visible to everyone, so a shared clock or logger needs no grant.
The global view fails closed the other way: a bare `container.resolve(Store)`
declares no domain, so it cannot reach a module-owned row.

## A worked example: two packages

`billing` owns `Store`; `shipping` owns `Shipment`, whose constructor needs one.
Each package declares its own view:

<!-- docs-contract: run -->
```python
class ShippingModule:
    pass


@injectable()
class Shipment:
    def __init__(self, store: Store) -> None:
        self.store = store


packages = DIContainer()
packages.register(Store, owning_module=BillingModule)
packages.register(Shipment, owning_module=ShippingModule)
packages.set_visible(BillingModule, frozenset())
packages.set_visible(ShippingModule, frozenset())

try:
    await packages.resolve(Shipment, owning_module=ShippingModule)
except MissingProviderError as exc:
    print(exc.detail["reason"])
    print(exc.detail["hint"])
```

```text
not_visible
grant Store to ShippingModule (set_visible or grant_visible), or resolve it from BillingModule
```

`Shipment` is shipping's own row, so it is visible; the blocked edge is `Store`.
The hint names both fixes — take the first:

<!-- docs-contract: run -->
```python
packages.grant_visible(ShippingModule, frozenset({Store}))
shipment = await packages.resolve(Shipment, owning_module=ShippingModule)
print(type(shipment.store).__name__)
```

That prints `Store`. One line changed: `ShippingModule` still knows nothing
about how `Store` is built, and billing keeps control of its constructor
arguments.

## Grants cross one edge

Only the edge being crossed is checked. Here `PricedStore` (billing) needs
`Rates` (pricing), and shipping asks only for `PricedStore`:

<!-- docs-contract: run -->
```python
class PricingModule:
    pass


@injectable()
class Rates:
    pass


@injectable()
class PricedStore:
    def __init__(self, rates: Rates) -> None:
        self.rates = rates


packages.register(Rates, owning_module=PricingModule)
packages.register(PricedStore, owning_module=BillingModule)
packages.set_visible(PricingModule, frozenset())
packages.set_visible(BillingModule, frozenset({Rates}))
packages.grant_visible(ShippingModule, frozenset({PricedStore}))

crossing = await packages.resolve(PricedStore, owning_module=ShippingModule)
print(type(crossing.rates).__name__)
```

It prints `Rates`, resolved from **billing's** view rather than shipping's, so
shipping never needed a grant for it. Revoke billing's grant and the resolve
fails with `detail["module"] == "BillingModule"` — the module that actually
needs the row, not the one that asked for it.

## Diagnosing a miss

`explain(token, owning_module=...)` returns a deterministic multi-line report.
It never raises and never constructs anything, so it is safe to call from a
debugger or a test:

<!-- docs-contract: run -->
```python
diagnosed = DIContainer()
diagnosed.register(Store, owning_module=BillingModule)
print(diagnosed.explain(Store))
diagnosed.set_visible(BillingModule, frozenset())
diagnosed.set_visible(ShippingModule, frozenset())
print(diagnosed.explain(Store, owning_module=ShippingModule))
```

```text
token: Store
domain: flat registry
row 1: owner=BillingModule visible=yes
status: ok
token: Store
domain: ShippingModule (declared)
row 1: owner=BillingModule visible=no remedy="grant Store to ShippingModule"
status: not_visible
```

A caller that never declared a domain at all:

<!-- docs-contract: run -->
```python
class ReportingModule:
    pass


print(diagnosed.explain(Store, owning_module=ReportingModule))
```

```text
token: Store
domain: ReportingModule (undeclared)
row 1: owner=BillingModule visible=no remedy="grant Store to ReportingModule"
status: undeclared
```

After a grant the report reads `visible=yes` and `status: ok`. From the global
view the same row reports `domain: global view`, `visible=no` and
`remedy="resolve Store from BillingModule"`. Unknown tokens report `row: none`
and `status: unregistered`.

## The error it raises

A blocked resolve raises `MissingProviderError` with `code="missing_provider"`
and a `detail` mapping carrying the reason and a remediation hint:

<!-- docs-contract: run -->
```python
try:
    diagnosed.get_provider(Store, owning_module=ReportingModule)
except MissingProviderError as exc:
    assert exc.code == "missing_provider"
    assert exc.detail["reason"] == "undeclared_module"
    assert exc.detail["token"] == "Store"
    assert exc.detail["module"] == "ReportingModule"
    print(exc.detail["hint"])
```

```text
call set_visible(ReportingModule, ...) to declare what it may see
```

| detail key | value |
|---|---|
| `reason` | `not_visible`, or `undeclared_module` when the caller never declared |
| `token` | the blocked token's name, as a string |
| `module` | the caller's module name, or `<global>` |
| `hint` | the remediation sentence, naming both fixes when there are two |

`str(exc)` is the one-line summary: `module ReportingModule has no declared
visibility`.

## Inspecting a view

`visible_in(module_cls)` returns the module's declared set — its own tokens plus
its grants:

<!-- docs-contract: run -->
```python
diagnosed.grant_visible(ShippingModule, frozenset({Store}))
print(sorted(token.__name__ for token in diagnosed.visible_in(ShippingModule)))
print(repr(diagnosed.visible_in(ReportingModule)))
```

```text
['Store']
frozenset()
```

It reports the *declaration*, not the full effective visibility: global rows are
visible to `ShippingModule` too and are not listed, and an undeclared module
answers with an empty set rather than an error. When the question is "would this
resolve, and why not", ask `explain` instead.

## Turning it off

Do nothing. If you never call `set_visible` or `grant_visible`, every
`owning_module=` argument is recorded but never consulted, and resolution
behaves exactly like the flat registry. That is the compatibility guarantee:
code written for a flat container keeps working after rows gain owners.

## Next

[Scopes and lifecycle](guides/scopes.md) covers what a row's lifetime does across
a request; it is declared separately but composes with visibility.
[Providers and injection](guides/providers.md) covers the registration surface
these grants address. [CONTRIBUTING.md](https://github.com/lauren-framework/lauren-di/blob/main/CONTRIBUTING.md) lists the gates that
execute every block on this page, and [the examples](https://github.com/lauren-framework/lauren-di/blob/main/examples/README.md) are
runnable programs for both surfaces.
