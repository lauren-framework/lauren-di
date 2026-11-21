# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-21

First public release: the dependency-injection core of the `lauren` framework,
extracted into a standalone package that does nothing but dependency injection.

### Goals delivered (PRD 01 §2)

Each goal below has an acceptance signal from PRD 01 §2, and each signal is now
checked in CI rather than asserted here.

| Goal | What shipped, and where it is checked |
|------|--------------------------------------|
| G1 | `lauren-di` installs and `import lauren_di` works in a fresh venv with nothing else on the path — `tests/packaging/test_wheel.py` |
| G2 | The §5.2 quickstart runs in a bare process with no `lauren` import — `tests/integration/test_quickstart.py` |
| G3 | The familiar vocabulary ships unchanged (`injectable`, `Scope`, `Token`, `Inject`, `Depends`, `use_*`, `DIContainer`, `Provider`, `InjectableMeta`) — `tests/unit/test_public_api.py` |
| G4 | `dependencies = []`, and every `Requires-Dist` in the wheel is `extra ==`-gated — `tests/packaging/test_wheel.py` |
| G5 | `requires-python = ">=3.10"` with CI on 3.10 – 3.14 — `tests/unit/test_noxfile_contract.py` |
| G6 | `py.typed` ships and both strict type checkers pass over the public surface — `nox -s typecheck typing` |

### Added

- `DIContainer` with explicit registration (`register`, `register_class`,
  `register_instance`, `register_value`, `register_factory`, `register_alias`,
  `register_custom`), compile-time validation (`compile`) and async resolution
  (`resolve`).
- `@injectable()` on classes and functions, with optional `scope`, `provides`
  and `multi` arguments, plus `injectable_meta()` for read-only inspection.
- `Scope` (`TRANSIENT` < `REQUEST` < `SINGLETON`) with the compile-time
  narrowing rule: a consumer may not outlive the instance it receives.
- `ResolutionScope` (via `DIContainer.scope()`) owning the request cache and
  reverse-creation-order teardown.
- Lifecycle hooks `@post_construct` / `@pre_destruct`, fired in MRO order
  (bases first on startup, subclasses first on teardown) for every scope.
- Custom-provider recipes `use_value`, `use_class`, `use_factory`,
  `use_existing`; `Token` for non-class bindings; `Inject`, `OptionalDep` and
  the spelling alias `Depends`.
- Multi-bindings (`multi=True`) resolved as `list[T]`, and single-provider
  guarantees for non-multi tokens.
- Build-time overrides: `OverridePlan`, `ProviderOverride`, `building_with`,
  `active_override_plan`, with per-build activation isolated by `ContextVar`.
- Opt-in module visibility: `owning_module`, `set_visible`, `grant_visible`,
  `visible_in` and `explain()` diagnostics; `None` keeps the flat registry.
- Error catalogue under a single `DIError` base, each with a stable `code`,
  a `detail` mapping, `to_payload()` and `as_json()`.
- Standard library only, `py.typed`, Python 3.10 – 3.14.

### Documentation

- `README.md` with a runnable quickstart and an explicit list of what the
  library does *not* do.
- An MkDocs site (`docs/`) in the PRD 01 §9 layout: `index.md`,
  `getting-started.md`, `guides/{providers,scopes,custom-providers,testing}.md`,
  plus `visibility.md`, `errors.md`, `migration.md` and a `reference.md` that is
  generated from `lauren_di.__all__` and the docstrings; built in strict mode by
  `nox -s docs`.
- `llms.txt` (the index: every public symbol named, every link resolvable) and
  `llms-full.txt` (one section per public symbol, with a runnable example), both
  enforced in CI by `scripts/check_llms_full.py`.
- `scripts/check_docs_contract.py` treats the docs as tests: nav integrity,
  compilable code blocks, executed `run` blocks, API coverage, and a freshness
  check that fails when `docs/reference.md` is not what the generator would
  write.
- `CONTRIBUTING.md` with the "is this DI?" boundary test from §3, the parity
  requirement and the two documentation gates, and `CLAUDE.md` / `AGENTS.md`
  describing this package's conventions and invariants for coding agents.

[Unreleased]: https://github.com/lauren-framework/lauren-di/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lauren-framework/lauren-di/releases/tag/v0.1.0
