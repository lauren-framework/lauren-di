# AGENTS.md — Instructions for Coding Agents

This repository accepts contributions from AI coding agents (Claude Code,
Cursor, Aider, Codex, Continue). This file is the human-readable mirror of
`CLAUDE.md` and the entry point for agents that follow the [agents.md][agents-md]
convention. When the two disagree, `CLAUDE.md` wins; when either disagrees with
the code, the code wins and both files are out of date.

[agents-md]: https://agents.md

## What this project is

`lauren-di` is a dependency-injection container for Python 3.10+, distributed as
`lauren-di`, imported as `lauren_di`. Standard library only, PEP 561 typed,
zero runtime dependencies.

It does dependency injection and nothing else. There is no ASGI or HTTP layer,
no request objects, no routing, no extractors, no configuration loader, no
scheduler and no module-graph compiler. A patch that needs any of those is out
of scope for this package, however reasonable the feature sounds.

## Quick start

```bash
pip install -e ".[dev]"

pytest -q                      # full suite: 558 tests (532 on 3.10); +52 parity with ../lauren-framework
pytest tests/unit -q           # one layer
pytest -k "visibility" -q      # one topic

nox -l                         # every task the project defines
nox -s ci                      # everything CI runs — the definition of done
```

`nox` is the canonical runner and CI runs the same sessions with the same
arguments, so local green means pipeline green. If `nox` is unavailable,
`pytest` plus `ruff check lauren_di tests scripts noxfile.py` and
`mypy` cover most of it — but `nox -s ci` is the real gate.

## Ground rules

1. **Run `nox -s ci` before proposing a patch.** It runs lint, format, typecheck,
   the 3.10–3.14 matrix, coverage with a fail-under gate, the llms/docs/PRD
   corpus contracts, and the build. A patch that fails it is not finished.
2. **Never weaken a gate to make a run green.** No lowering `fail_under`, no
   deleting or `xfail`-ing an assertion, no blanket `# noqa`, no narrowing a
   session to the tests that pass, no `--no-verify`. The difference between a
   fixed check and a hidden one does not show up in a diff, so this rule is
   absolute.
3. **No runtime dependencies.** `dependencies` in `pyproject.toml` stays empty.
   Test and docs tooling belongs in the `dev` / `docs` / `release` extras.
4. **Do not hand-edit generated files.** `llms-full.txt` comes from
   `docs/reference.md` via `python scripts/build_llms_full.py`; `site/` comes from
   `nox -s docs`; `dist/` comes from `nox -s build`. `.gitignore` covers the
   artefacts and caches, but **not** `docs/reference.md` or `llms-full.txt`: they
   are reviewed in the diff and checked by CI, so they are tracked like source.
   `tests/unit/test_gitignore_contract.py` fails if either half stops being true.
5. **Public API is a contract.** `lauren_di.__all__` has 38 names. Adding one
   means also documenting it in `docs/reference.md` (regenerate the page),
   regenerating `llms-full.txt`, and passing `nox -s llms_check`. Removing or
   renaming one is a breaking change and needs a CHANGELOG entry.
6. **Tests are async-aware but not pytest-asyncio-based.** Drive coroutines with
   `helpers.run(...)`; do not add `async def test_*` functions.
7. **Error codes are API.** Each exception subclasses `DIError` and carries a
   stable `code`. Renaming a code breaks callers; treat it as a major change.

## Library conventions (PRD 01 §9)

Four rules shape every patch. They are not style preferences: each is checked
somewhere, and the check is named so that a disagreement between the rule and
the code is a bug in one of the two.

1. **Standard library only.** `dependencies = []` in `pyproject.toml`, and the
   only extras are contributor-only (`dev`, `release`, `docs`). A patch that
   needs a third-party import is out of scope, not a packaging decision.
   *Checked by* `tests/packaging/test_wheel.py` and
   `tests/packaging/test_no_framework_imports.py`.
2. **No HTTP vocabulary.** No `Request`, `Response`, headers, cookies, bodies,
   status codes, routes, controllers, extractors or ASGI scopes — not in the
   code, not in `__all__`, and not in a docstring that would teach a reader to
   expect one. Runtime values arrive as an opaque `values` mapping.
   *Checked by* `tests/unit/test_non_goals.py`.
3. **`__all__` discipline.** `lauren_di.__all__` *is* the public surface: 38
   names, sorted, with `RUF022` enforcing the order. Adding a name is a
   three-artefact change in one commit — `__all__`, `docs/reference.md`
   (regenerated), `llms-full.txt` (regenerated) — and removing or renaming one
   is a breaking change with a CHANGELOG entry.
   *Checked by* `tests/unit/test_public_api.py` and `nox -s llms_check
   docs_contract`.
4. **Parity with the framework.** `lauren_di` is the standalone extraction of
   `lauren/_di`, so shared behaviour stays identical: a divergence is either a
   bug here or a deliberate improvement recorded in PRD 08's matrix with a
   written justification *before* it ships. Review against
   `../lauren-framework`; `tests/parity/` is the automated gate (PRD 08).

## Where to look first

| Question | File |
|---|---|
| What is being built next, and how is "done" proved? | `GOALS.md` |
| What does the public surface promise? | `prds/01-product-definition-and-public-api.md` |
| Why does registration behave this way? | `prds/02-core-container-and-provider-registration.md` |
| Scope rules and lifecycle ordering | `prds/03-scopes-lifecycle-and-disposal.md` |
| Tokens, recipes, multi-bindings | `prds/04-custom-providers-tokens-and-multi-bindings.md` |
| Every exception and its `detail` keys | `prds/05-compile-time-validation-and-errors.md` |
| Why visibility works the way it does | `prds/06-module-visibility-decision-record.md` |
| Overrides and test isolation | `prds/07-testing-overrides-and-isolation.md` |
| Symbol-by-symbol migration notes | `prds/08-framework-compatibility-and-migration.md` |
| How resolution is implemented | `lauren_di/_container.py` |
| How the project is checked | `noxfile.py`, `.github/workflows/` |

The design records in `prds/` are the *reason* the code looks the way it does.
Read the relevant one before changing behaviour; the docstrings cite them.

## Invariants you must not break

- `compile()` validates, `resolve()` dispatches — never move validation into the
  request path.
- `Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON`: a consumer may not outlive
  its dependency.
- `@post_construct` runs bases-first, `@pre_destruct` runs subclasses-first;
  hook methods need distinct names across a hierarchy.
- `Inject(token)` is `Annotated` metadata only — as a default value it injects
  nothing.
- `OptionalDep(token)` is valid only inside `use_factory(injects=...)`.
- Visibility is opt-in and, once switched on, container-wide.
- `building_with(plan)` wraps registration, not resolution.
- Teardown collects `DestructError`s and continues; it never aborts early.
- `resolve`, `startup` and `shutdown` are coroutines; `compile` and `scope` are
  not.

## Patch checklist

- [ ] Behaviour change has a test that fails before the fix and passes after.
- [ ] `nox -s ci` is green.
- [ ] New or changed public name is documented in `docs/reference.md` and
      `llms-full.txt` is regenerated.
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` for any user-visible change.
- [ ] No new runtime dependency, no new `# noqa`, no lowered threshold.
- [ ] Commit message says why, not just what.
