# Troubleshooting

Start from the error you are holding: what each `code` means, why the container
refused, and the smallest change that fixes it. If you only have a symptom
(something resolved twice, a hook that never fired), start from
[when you have a symptom](#when-you-have-a-symptom) instead.

Every failure is a leaf of `DIError`, so `except DIError` catches all of them and
`error.code` is the stable string used below. The full catalogue with `detail`
keys lives in [errors.md](errors.md); scope rules are in
[guides/scopes.md](guides/scopes.md).

## Find your error

The rows are in the order a program meets them: registration, then compile,
then resolve, then teardown.

| stage | `code` | what it means | the one-line fix |
| --- | --- | --- | --- |
| any | `lauren_di_error` | The root class itself. Never raised; you are holding it because you wrote `except DIError` and re-raised, or because you constructed it in a test. | Catch a subclass, or check `error.code` before branching. |
| registration | `decorator_usage` | A token is not hashable: `use_value: provider token [1, 2] is not hashable. DI tokens must be classes, strings, or Token instances.` Also raised when a hook decorator is applied to something that is not a method. | Use a class, a string, or `Token("name")` as the token. |
| registration | `invalid_provider` | You registered something that is neither a class nor a callable: `Cannot register 42: expected a class or a callable`. | Register the class itself or a factory function; wrap constants with `use_value`. |
| registration | `missing_provider` | The class was registered but never decorated: `Plain is not marked @injectable`. | Add `@injectable()` to the class, or register a factory that builds it. |
| registration | `duplicate_binding` | The same token is registered twice: `Dup is already registered`. | Register it once, or pass `multi=True` when you really want several bindings. |
| registration | `metadata_inheritance` | A subclass picked up the marker of its base: `Sub2 inherited injectable metadata from Base2; re-decorate explicitly.` | Decorate the subclass with `@injectable()`; markers are never inherited on purpose. |
| registration | `lifecycle_config` | A hook was attached to a non-method (`'nope' is not a method; decorate a method definition`), or a generator provider was declared `TRANSIENT`. | Decorate a real method; give generator providers a scope with a teardown owner. |
| registration | `override_error` | The override payload does not fit the row: `cannot override values Svc: non_multi_values`, or `cannot override class 'x': bad_payload`. | Use `values` only on `multi=True` rows; the `class` kind needs `use_class` or `use_factory`. |
| compile | `protocol_ambiguity` | Two providers claim one token: `Multiple bindings for Repo (2)`. | Keep one implementation, or resolve the concrete class instead of the `Protocol`. |
| compile | `circular_dependency` | A cycle of classes or of named aliases: `Circular dependency: A -> B -> A`. | Break the loop with a factory, or point the alias at a narrower token. |
| compile | `di_scope_violation` | A consumer outlives its dependency: `Singleton W depends on request-scoped R`. | Widen the dependency's scope, or narrow the consumer to `Scope.REQUEST`. |
| compile | `unresolvable_parameter` | A provider exists but its payload cannot build the parameter, e.g. `use_class` given something that is not a class. | Pass a class to `use_class`; if you meant an instance, use `use_value`. |
| resolve | `missing_provider` | The token has no row at all: `No provider for Plain`. | Register the token, or fix the annotation so it names the registered token. |
| resolve | `lifecycle_violation` | You resolved through a scope that already exited: `Cannot resolve R inside a closed scope`. | Resolve inside the `async with` block, or use `container.resolve` for singletons. |
| teardown | `destruct_error` | A `@pre_destruct` hook raised: `@pre_destruct on Fragile failed during shutdown teardown: boom`. | Make teardown non-raising; `shutdown()` returns these instead of raising. |

`detail` is always a mapping, never a formatted string: `di_scope_violation`
carries `dependent`, `dependent_scope`, `dependency`, `dependency_scope`, and
`alias` when the edge goes through a named alias; `circular_dependency` carries
`cycle`; `duplicate_binding` carries `token`, `existing`, `new`.

## When you have a symptom

These are the cases where nothing was raised, so there is no `code` to look up.

| symptom | why | what to check |
| --- | --- | --- |
| A class-body field is never injected | Only annotated attributes **without** a default are injected. A field with any default (`x: Dep = None`), a `ClassVar`, and an unannotated attribute are all left alone. Private names are not special: `_dep: Dep` is injected. | `print(vars(instance))` and confirm the field is absent, then delete the default. |
| Two instances where you expected one | `Scope.TRANSIENT`, and every factory row, build a fresh object per resolve. Singletons do not. | Look at `row.scope` in the provider table below, and compare with `is`. |
| Teardown never ran | Hooks run at the scope that owns the instance: singletons at `await container.shutdown()`, request-scoped objects when the `async with container.scope()` block exits. A request-scoped object resolved outside a scope warns that it will never be torn down and indeed never is. | Confirm the object's scope, then confirm you awaited `shutdown()` or exited the block. |
| A hook never fired | Hooks belong to objects the container constructs. A factory that returns an already-built instance runs no `@post_construct`. | Resolve the class directly instead of a factory, or move the hook into the factory. |
| An override did not apply | Overrides match on token identity, silently. A plan keyed on `Token("greeter")` does not touch a row registered under the string `"greeter"`. | Key the plan with the same token object you registered. |

## A failing case, start to finish

A singleton that wants a request-scoped dependency fails at compile time, not at
the call site, which is the point of compiling eagerly.

```python fragment
from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.REQUEST)
class Repo:
    pass


@injectable()  # singleton
class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo


container = DIContainer()
container.register(Repo)
container.register(Service)
container.compile()
# DIScopeViolationError: Singleton Service depends on request-scoped Repo
```

The message names both ends and both scopes, so the fix is a one-word edit:
`@injectable(scope=Scope.REQUEST)` on `Service`, which then follows the request
that owns the `Repo` and is torn down with it.

## When in doubt

Three diagnostics, in increasing order of specificity.

1. Print the provider table. `container.all_providers()` returns one row per
   registration with `cls`, `scope`, `provides`, `multi`, `deps`, and the hook
   slots; `container.explain(token)` returns a short text report for one token,
   including which module owns the row and whether it is visible.
2. Compile eagerly. `container.compile()` turns resolve-time surprises
   (ambiguity, cycles, scope violations) into errors at startup, where the stack
   still contains your registration code.
3. Resolve the dependency directly. `await container.resolve(Dep)` for a single
   token isolates the failure from the class that wanted it, and a request-scoped
   token resolved through `async with container.scope() as scope: await scope.resolve(Dep)`
   proves the scope is the problem and not the graph.

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, injectable


@injectable()
class Clock:
    pass


@injectable()
class Service:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock


async def main() -> None:
    container = DIContainer()
    container.register(Clock)
    container.register(Service)
    container.compile()

    for row in container.all_providers():
        print(row.scope.name, row.cls.__name__, row.deps)

    print(container.explain(Service))
    service = await container.resolve(Service)
    print(type(service.clock).__name__)
    await container.shutdown()


asyncio.run(main())
```

If the table shows the row you expected and `explain` says `status: ok`, the
container is fine and the bug is in the call site. If the row is missing, the
registration never ran — check the import, not the container.


## Pinning a failure in a test

`code` and `detail` are the stable surface, so assert on them rather than on the
formatted message, which is free to change:

```python
import pytest

from lauren_di import DIContainer, DIError, injectable


@injectable()
class Clock:
    pass


def test_double_registration_is_reported() -> None:
    container = DIContainer()
    container.register(Clock)
    with pytest.raises(DIError) as caught:
        container.register(Clock)
    assert caught.value.code == "duplicate_binding"
    assert caught.value.detail["token"] == "Clock"
```

The same pattern works for `di_scope_violation` (`dependent`, `dependency`) and
`circular_dependency` (`cycle`), which is how you assert *why* the container
refused rather than only *that* it refused.

## Reporting a problem

When a message does not explain the failure, the three facts worth including are
the `code`, the full `detail` mapping, and the row that `container.explain(token)`
prints for the token in the message. Those three usually identify the bug without
the rest of the program.

Related reading: [errors.md](errors.md) for the full catalogue, [recipes.md](recipes.md)
for working configurations, [guides/scopes.md](guides/scopes.md) for the scope
ordering rules that `di_scope_violation` enforces.
