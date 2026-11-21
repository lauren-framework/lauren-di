# Errors

Every failure is an instance of `DIError`. Each subclass has a stable `code`,
an optional `detail` mapping, and two serialisation helpers:

```python
from lauren_di import DIError, MissingProviderError


error = MissingProviderError("no row for Thing", detail={"token": "Thing"})
assert isinstance(error, DIError)
assert error.code == "missing_provider"
assert error.detail == {"token": "Thing"}
assert error.to_payload()["code"] == "missing_provider"
assert '"code"' in error.as_json()
```

## Catalogue

| exception | `code` | raised when | raised by |
|---|---|---|---|
| `DIError` | `lauren_di_error` | base class; never raised directly | — |
| `DecoratorUsageError` | `decorator_usage` | `@injectable` / `use_*` used incorrectly (unhashable token, `None` token) | `injectable`, `use_value`, `use_class`, `use_factory`, `use_existing` |
| `InvalidProviderError` | `invalid_provider` | the target cannot be a provider (not a class/function, missing metadata) | `register`, `register_custom` |
| `MissingProviderError` | `missing_provider` | no visible row for the requested token | `resolve`, `get_provider`, `compile` |
| `DuplicateBindingError` | `duplicate_binding` | a non-`multi` token is bound twice | `register*` |
| `ProtocolAmbiguityError` | `protocol_ambiguity` | two non-`multi` implementations of one Protocol in the same domain | `compile` |
| `CircularDependencyError` | `circular_dependency` | the dependency graph contains a cycle, including alias loops | `compile` |
| `DIScopeViolationError` | `di_scope_violation` | a consumer outlives its dependency, or a request-scoped provider is resolved in strict mode outside a scope | `compile`, `resolve` |
| `UnresolvableParameterError` | `unresolvable_parameter` | a constructor/field annotation cannot be turned into a token | `register`, `compile` |
| `MetadataInheritanceError` | `metadata_inheritance` | a subclass inherits `@injectable` metadata without re-decorating | `register` |
| `LifecycleConfigError` | `lifecycle_config` | a `@post_construct` / `@pre_destruct` declaration is malformed | `post_construct`, `pre_destruct`, `startup` |
| `LifecycleViolationError` | `lifecycle_violation` | an instance is used after its owning scope closed | `ResolutionScope.resolve`, `close` |
| `DestructError` | `destruct_error` | a `@pre_destruct` hook raised; collected, never re-raised | `shutdown`, `ResolutionScope.close` |
| `OverrideError` | `override_error` | an override is malformed or its target is missing | `ProviderOverride`, `OverridePlan`, `compile` |

## `detail` keys worth asserting on

| situation | keys |
|---|---|
| token not visible | `token`, `module`, `reason` (`not_visible` / `undeclared_module`), `hint` |
| request-scoped outside a scope | `token`, `reason="no_active_scope"` |
| scope narrowing violated | `dependent`, `dependency`, `dependent_scope`, `dependency_scope` |
| cycle | `cycle` (the token names in order) |
| override refused | `reason` (`bad_payload`, `target_missing`, `non_multi_values`, `scope_widen`), `token` |
| teardown failure | `hook`, `stage`, `origin` |

Assert on `code` rather than on message text: messages are for humans and may be
reworded, codes are part of the contract.

## DestructError is returned, not raised

`shutdown()` and `ResolutionScope.close()` return a tuple of `DestructError`.
A failing hook never aborts the remaining teardown, so you get every failure at
once:

```python
import asyncio

from lauren_di import DIContainer, injectable, pre_destruct


@injectable()
class Fragile:
    @pre_destruct
    async def fail(self) -> None:
        raise RuntimeError("boom")


async def main() -> None:
    container = DIContainer()
    container.register(Fragile)
    await container.startup()
    await container.resolve(Fragile)
    for error in await container.shutdown():
        print(error.code, error.detail["hook"])


asyncio.run(main())
```
