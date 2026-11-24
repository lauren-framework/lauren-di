# lauren-di

A standalone dependency-injection container: explicit registration, typed
scopes, lifecycle hooks, opt-in module visibility. Standard library only,
Python 3.10+, PEP 561 typed.

## What it is

`lauren_di` is a container you drive yourself:

- you **register** what exists,
- you **resolve** what you need, and
- you decide **when** a scope begins and ends.

There is no global registry, no import-time discovery, and no framework
attached. If you can call `await container.resolve(Thing)`, you know
everything the library does.

## What it is not

No ASGI application, no routing, no request objects, no extractors, no
configuration system, no scheduler, no module-graph compiler. Those belong to a
higher layer. `lauren_di` keeps the seam (`value_tokens`, `field_predicate`,
container-owned `startup()` / `shutdown()`) so an application can plug in
without becoming a dependency of this library.

## Design principles

1. **Explicit over implicit.** Every binding is a `register` call. Nothing is
   inferred from module layout.
2. **Fail at compile time, not at first use.** `container.compile()` walks the
   graph and raises before your process serves a single request.
3. **Scopes are types, not tags.** `Scope` is an ordered enum and the ordering
   is load-bearing: a consumer may not outlive the instance it receives.
4. **Errors are data.** Every exception carries a stable `code` and a `detail`
   mapping you can log or assert on.
5. **Standard library only.** No runtime dependencies, ever.

## Where to go next

**Start here**

- [Getting started](getting-started.md) — install, declare, register, resolve, tear down.
- [Providers and injection](guides/providers.md) — constructor, field and function providers.

**Guides**

- [Scopes and lifecycle](guides/scopes.md) — the part that bites.
- [Custom providers and tokens](guides/custom-providers.md) — non-class bindings and `list[T]`.
- [Testing and overrides](guides/testing.md) — per-build substitution and fixtures.
- [Hosting in a framework](guides/hosting.md) — the host-adapter contract.
- [Recipes](recipes.md) — task-shaped, complete wiring for common jobs.
- [Module visibility](visibility.md) — opt-in encapsulation.
- [Errors](errors.md) — the full catalogue.
- [Troubleshooting](troubleshooting.md) — start from the error you are holding.
- [Async and concurrency](concurrency.md) — event loops, tasks, threads and lifetime.
- [Design notes](design.md) — why the rules are what they are.
- [Migration from lauren](migration.md) — coming from the framework.

**Reference**

- [Reference](reference.md) — every public symbol, generated from `__all__`.
- [CONTRIBUTING.md](https://github.com/lauren-framework/lauren-di/blob/main/CONTRIBUTING.md) — the
  "is this DI?" boundary test and the gates.
