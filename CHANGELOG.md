# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ResolutionScope.scope()` opens a **child scope**: any depth, a read-through
  cache (an ancestor's `REQUEST` instance is reused, only what a level creates is
  cached and torn down there) and `values` inherited by copy. New members
  `parent`, `depth` and `child_scopes()` report the chain (PRD 15, D-64 … D-66).
- Configurable nesting depth: `DIContainer(max_scope_depth=N)` is the container
  policy and `scope(max_depth=N)` narrows one subtree. Unlimited when both are
  `None`; an over-deep call raises `LifecycleViolationError`
  (`reason: "depth_exceeded"`) before anything is constructed, and a value below
  1 or a widened subtree raises `LifecycleConfigError` (D-67).
- `ResolutionScope.close()` closes the children nobody closed first, deepest
  first, so an instance is never torn down while a scope that reads through it is
  still resolvable (D-68). Both the close walk and the cache lookup are loops, so
  a 2,000-level chain resolves and closes without touching the interpreter's
  stack.

### Documentation

- `prds/16-extensible-scopes.md` specifies **extensible scopes**: `ScopeDef` /
  `ScopeSet` / `Carrier` replace the closed enum, `Scope` stays the default
  ladder with `TRANSIENT = 0` / `REQUEST = 1` / `SINGLETON = 2`, and an
  application declares its own levels per container. It measures the four
  defects that motivate it — the enum cannot be subclassed, a foreign scope
  compiles and is then cached nowhere and torn down never, the narrowing rule
  reads a foreign rank as "wider than everything", and the error path raises
  `AttributeError` — with D-71 … D-78 in the register, D-77 (which node caches a
  new instance) recorded as open for review. Proposed only: no code in this
  change.
- `docs/guides/scopes.md`'s "Nested scopes" section is rewritten around the
  child-scope semantics and gains "Capping the depth"; both examples execute in
  `nox -s docs_contract`.
- `prds/15-nested-resolution-scopes.md` specifies the semantics, the measured
  cost (flat ~3.3 µs per cache-hit resolve to depth 1,000; 462 B per level) and
  the alternatives weighed, with D-64 … D-70 in the register.

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
