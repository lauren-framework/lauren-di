# Module visibility

Visibility is **opt-in**. A fresh container is a flat registry: every row is
visible from every domain and `owning_module` is inert metadata. The first call
to `set_visible` or `grant_visible` turns encapsulation on for the whole
container.

## Declaring a row's owner

```python
from lauren_di import DIContainer, injectable


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

```python
container.set_visible(BillingModule, frozenset())          # own rows only
container.grant_visible(BillingModule, frozenset({Store}))  # plus Store
```

`set_visible` replaces the granted set; `grant_visible` unions into it. Both
invalidate the compiled graph, so a later `resolve` re-validates.

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

## Diagnosing a miss

`explain(token, owning_module=...)` returns a deterministic multi-line report:

```python
from lauren_di import DIContainer, injectable


class BillingModule:
    pass


class ShippingModule:
    pass


@injectable()
class Store:
    pass


container = DIContainer()
container.register(Store, owning_module=BillingModule)
print(container.explain(Store))
```

```text
token: Store
domain: flat registry
row 1: owner=Billing visible=yes
status: ok
```

After encapsulation is switched on and the caller is undeclared:

```text
token: Store
domain: Shipping (undeclared)
row 1: owner=Billing visible=no remedy="grant Store to Shipping"
status: undeclared
```

And once `ShippingModule` is declared but not granted the row:

```text
token: Store
domain: Shipping (declared)
row 1: owner=Billing visible=no remedy="grant Store to Shipping"
status: not_visible
```

Unknown tokens report `row: none` and `status: unregistered`.

## The error it raises

A blocked resolve raises `MissingProviderError` whose `detail` carries the
reason and a remediation hint:

```python
from lauren_di import DIContainer, MissingProviderError, injectable


class BillingModule:
    pass


class ShippingModule:
    pass


@injectable()
class Store:
    pass


container = DIContainer()
container.register(Store, owning_module=BillingModule)
container.set_visible(BillingModule, frozenset())
container.set_visible(ShippingModule, frozenset())

try:
    container.get_provider(Store, owning_module=ShippingModule)
except MissingProviderError as exc:
    assert exc.detail["reason"] == "not_visible"
    assert exc.detail["token"] == "Store"
    assert exc.detail["module"] == "Shipping"
    assert "hint" in exc.detail
```

## Inspecting a view

```python
visible = container.visible_in(ShippingModule)   # frozenset of tokens/rows
```

## Turning it off

Do nothing. If you never call `set_visible` or `grant_visible`, every
`owning_module=` argument is recorded but never consulted, and resolution
behaves exactly like the flat registry. That is the compatibility guarantee:
code written for a flat container keeps working after rows gain owners.
