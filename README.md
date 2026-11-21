<div align="center">
  <h1><i>lauren-di</i></h1>
</div>
<p align="center">
    <em>lauren-di: a standalone dependency-injection container for Python &mdash; explicit registration, typed scopes, lifecycle hooks and opt-in module visibility, standard library only, no runtime dependencies.</em>
</p>
<p align="center">
<a href="https://github.com/lauren-framework/lauren-di/actions/workflows/tests.yml?query=branch%3Amain+event%3Apush">
    <img src="https://github.com/lauren-framework/lauren-di/actions/workflows/tests.yml/badge.svg?branch=main&event=push" alt="Tests">
</a>
<a href="https://github.com/lauren-framework/lauren-di/actions/workflows/lint.yml?query=branch%3Amain+event%3Apush">
    <img src="https://github.com/lauren-framework/lauren-di/actions/workflows/lint.yml/badge.svg?branch=main&event=push" alt="Lint">
</a>
<a href="https://github.com/lauren-framework/lauren-di/actions/workflows/codeql.yml?query=branch%3Amain">
    <img src="https://github.com/lauren-framework/lauren-di/actions/workflows/codeql.yml/badge.svg?branch=main" alt="CodeQL">
</a>
<a href="https://pypi.org/project/lauren-di">
    <img src="https://img.shields.io/pypi/v/lauren-di?color=%2334D058&label=pypi%20package" alt="Package version">
</a>
<a href="https://pypi.org/project/lauren-di">
    <img src="https://img.shields.io/pypi/pyversions/lauren-di.svg?color=%2334D058" alt="Supported Python versions">
</a>
<a href="https://github.com/lauren-framework/lauren-di/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
</a>
<a href="https://github.com/astral-sh/ruff">
    <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff">
</a>
<a href="https://mypy.readthedocs.io/en/stable/">
    <img src="https://img.shields.io/badge/types-mypy-blue.svg" alt="Checked with mypy">
</a>
</p>

---

**Documentation**: <a href="https://lauren-framework.github.io/lauren-di/" target="_blank">https://lauren-framework.github.io/lauren-di/</a>

**Source Code**: <a href="https://github.com/lauren-framework/lauren-di" target="_blank">https://github.com/lauren-framework/lauren-di</a>

---

## For AI Agents & Coding Assistants

The public surface is mirrored for coding agents. Both mirrors are generated
from `lauren_di.__all__` and gated in CI (`nox -s llms_check`), so neither can
describe a version of the library that no longer exists.

| Resource | What it contains |
|---|---|
| [`llms.txt`](llms.txt) | The map &mdash; what the library is, the minimal example, one line per public symbol |
| [`llms-full.txt`](llms-full.txt) | The corpus &mdash; one section per public symbol, with signature, runnable example and error codes |
| [`AGENTS.md`](AGENTS.md) | Agent rules, by-task lookup, file ownership, common errors, definition of done |
| [`CLAUDE.md`](CLAUDE.md) | Conventions, commands, golden rules |

---

`lauren-di` is the DI core of the [lauren](https://lauren-py.dev) framework,
extracted so it can be used on its own. It does **one** thing — dependency
injection. There is no ASGI layer, no routing, no extractors, no scheduler and
no module-graph compiler.

## Install

```bash
pip install lauren-di
```

## Quickstart

```python
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

Resolution is asynchronous because construction may be (`__init__` can be sync,
but providers, factories and lifecycle hooks may all await). `Config` and
`Session` are singletons, so resolving `Config` twice hands back the same object
and `Session` reads one `dsn`. `UserRepo` is `Scope.REQUEST`, so it exists only
inside `async with container.scope()`: each scope builds its own instance and
tears it down on exit. `startup()` fires `@post_construct` hooks; `shutdown()`
tears everything down in reverse creation order.

## What you get

- **Explicit registration.** `container.register(...)` and friends; nothing is
  discovered by import side effects.
- **Typed scopes.** `Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON`, with a
  compile-time rule that a consumer may not outlive the instance it receives.
- **Scope contexts.** `async with container.scope() as scope:` owns a request
  cache and tears its instances down on exit.
- **Lifecycle hooks.** `@post_construct` / `@pre_destruct`, fired in MRO order.
- **Custom providers.** `use_value`, `use_class`, `use_factory`, `use_existing`,
  plus `Token` for bindings that are not classes.
- **Multi-bindings.** `multi=True` registrations resolve as `list[T]`.
- **Opt-in module visibility.** `owning_module` + `set_visible`; leave it alone
  and you get a flat registry.
- **Build-time overrides.** Per-build `OverridePlan`s, isolated by `ContextVar`.
- **A real error catalogue.** One `DIError` base, stable `code` strings,
  machine-readable `detail` mappings.

## Documentation

Everything ships in this repository, and the same pages are published at
<https://lauren-framework.github.io/lauren-di/>:

- [Getting started](docs/getting-started.md) — install, first container, first
  provider, first scope.
- [Providers and injection](docs/guides/providers.md) — constructor and
  class-body injection, `Token` bindings, multi-bindings.
- [Scopes and lifecycle](docs/guides/scopes.md) — the scope ordering, request
  caches, `@post_construct` / `@pre_destruct`.
- [Custom providers](docs/guides/custom-providers.md) — `use_value`,
  `use_class`, `use_factory`, `use_existing`.
- [Testing and overrides](docs/guides/testing.md) — per-build `OverridePlan`s,
  isolated by `ContextVar`.
- [Visibility](docs/visibility.md) — opt-in module boundaries.
- [Errors](docs/errors.md) — the catalogue behind `DIError`.
- [Reference](docs/reference.md) — every public symbol, generated from
  `lauren_di.__all__`.
- [Migrating from the framework](docs/migration.md) — moving a container out of
  `lauren`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the boundary test that decides whether a
feature belongs here, and for the gates a patch must pass.

```bash
nox -s tests        # 3.10 – 3.14
nox -s ci           # everything CI runs
nox -l              # list every session
```

## Licence

MIT — see [LICENSE](LICENSE).
