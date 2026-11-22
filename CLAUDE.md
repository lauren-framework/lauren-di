# CLAUDE.md

Guidance for coding agents (Claude Code, Cursor, Aider, Codex, Continue) working
in this repository. It records the conventions that make an AI-generated patch
land without a review loop: the commands that define "done", the invariants that
must not be broken, and the files that are generated rather than edited.

> `lauren-di` is a **dependency-injection container and nothing else**. It has no
> ASGI layer, no HTTP request objects, no routing, no extractors, no
> configuration system, no scheduler and no module-graph compiler. If a proposed
> change needs any of those, it belongs in a different package.

## 0. Commands

`nox` is the canonical task runner; every session installs the package editable
first, so no separate `pip install` is needed. CI runs the same sessions with the
same arguments, so a green `nox` locally means a green pipeline.

```bash
nox -l                              # list every session with its docstring
nox                                 # fast default set (lint, unit, integration, typecheck)
nox -s ci                           # everything CI runs — run this before proposing a patch

# Tests
nox -s tests                        # whole suite on 3.10, 3.11, 3.12, 3.13, 3.14
nox -s unit                         # tests/unit only
nox -s integration                  # tests/integration only
nox -s coverage                     # branch coverage, fails below pyproject's fail_under

# Quality
nox -s lint                         # ruff check (no writes)
nox -s format                       # ruff format --check + ruff check
nox -s format -- --apply            # rewrite files instead of failing
nox -s typecheck                    # mypy

# Docs and agent references
nox -s docs                         # mkdocs build --strict into site/
nox -s docs_contract                # docs-as-tests: nav, code blocks, API coverage
nox -s llms_check                   # every lauren_di.__all__ symbol documented with an example
nox -s prds                         # prds/check_consistency.py: index, template, links,
                                    # cross-refs, decisions, symbols, scope, baseline owners

# Packaging
nox -s build                        # sdist + wheel into dist/
nox -s build_check                  # twine check --strict
nox -s release_test                 # build, validate, prove the wheel installs (no upload)
nox -s clean                        # remove build artefacts, caches and site/
```

Arguments after `--` are **appended** to the session's own arguments, never
substituted for them. `nox -s unit -- -q` still selects `tests/unit`;
`nox -s coverage -- --cov-report=xml` still measures coverage. Do not reintroduce
the `posargs or defaults` spelling — it once turned the CI coverage job into a
no-op, and `tests/unit/test_noxfile_contract.py` now guards against it.

Direct pytest is fine for fast iteration once the package is installed:

```bash
pip install -e ".[dev]"
pytest tests/unit/test_resolution.py -q
pytest -k "visibility and not explain" -q
```

## 1. Identity and non-goals

- Distribution `lauren-di`, import `lauren_di`. Both matter: the hyphen is PyPI,
  the underscore is code.
- Standard library only. **No runtime dependencies** — a patch that adds a
  `dependencies = [...]` entry needs an explicit human decision, not an agent's.
- Python 3.10 – 3.14, PEP 561 typed (`lauren_di/py.typed` ships in the wheel).
- Non-goals, to be stated as *absences* rather than promised as features: HTTP
  request objects, routing, extractors, scheduling, configuration loading, and
  the module-graph compiler.

## 2. Layout

```
lauren_di/
  __init__.py        public surface; __all__ is the contract (38 names)
  _container.py      DIContainer: registration, compile(), resolve(), lifecycle
  _decorators.py     @injectable, @post_construct, @pre_destruct, InjectableMeta
  _scope.py          Scope, ResolutionScope (the request cache + teardown owner)
  _overrides.py      OverridePlan, ProviderOverride, building_with, ContextVar
  _custom.py         Token, CustomProvider, use_value/use_class/use_factory/use_existing
  _errors.py         DIError hierarchy, stable `code` values, detail mappings
  types.py           Protocol-level typing helpers
  _typing/           vendored forward-reference resolver (excluded from coverage)
tests/unit/          one component in isolation
tests/integration/   several components wired together, incl. lifecycle and visibility
docs/                mkdocs pages; docs/reference.md is generated, and is the source for llms-full.txt
scripts/             build_llms_full.py, check_llms_full.py, check_docs_contract.py
prds/                design records — read 01–08 before changing public behaviour
```

## 3. How resolution works

1. **Register.** `container.register(Thing)`, `register_instance`,
   `register_value`, `register_factory`, `register_alias`, `register_custom`, or
   a `CustomProvider` recipe. Registration is explicit; nothing is discovered by
   import side effects.
2. **Compile.** `container.compile()` validates the graph — cycles, missing
   providers, ambiguity, scope violations, alias targets, unresolvable
   parameters, inherited metadata. It is synchronous and returns `None`; misuse
   fails here, not on the request path.
3. **Resolve.** `await container.resolve(token)` is the only entry point you
   need. Resolution is async because construction, factories and lifecycle hooks
   may all await.
4. **Lifecycle.** `await container.startup()` fires `@post_construct` for every
   scope; `await container.shutdown()` fires `@pre_destruct` in reverse creation
   order and returns a tuple of `DestructError` rather than raising on the first
   failure. `async with container.scope() as scope:` owns a request cache and
   tears its instances down on exit.

## 4. Invariants — breaking any of these is a bug, not a trade-off

1. `compile()` validates; `resolve()` dispatches. Never move validation into
   resolution.
2. `Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON` is load-bearing: a
   consumer may not outlive its dependency, so `SINGLETON` depending on
   `REQUEST` is an error.
3. `@post_construct` runs bases-first; `@pre_destruct` runs subclasses-first.
   Hooks across a hierarchy must use **distinct method names**.
4. `Inject(token)` is `Annotated` metadata only. As a parameter default it
   injects nothing.
5. `OptionalDep(token)` is valid only inside a `use_factory(injects=...)` list.
6. Visibility is opt-in. With no `set_visible`/`grant_visible` call the registry
   is flat and `owning_module` is inert. Once encapsulation is switched on it is
   container-wide: own rows stay visible, undeclared modules fail closed.
7. `building_with(plan)` wraps **registration**, not resolution.
8. Every exception subclasses `DIError` and carries a stable `code`. Codes are
   API; renaming one is a breaking change.
9. Teardown never aborts: collect `DestructError`s and continue disposing.

## 5. Conventions

- **Formatting and linting are not negotiable.** `ruff` is pinned in
  `noxfile.py` (0.15.22). Line length, import order and rule selection come from
  `pyproject.toml`; run `nox -s format -- --apply` rather than hand-wrapping.
- **Typing.** `mypy` runs over `lauren_di` with an explicit check set, not
  `--strict`; see the comment in `pyproject.toml` before adding checks. Public
  functions carry full annotations — `py.typed` is shipped, so the annotations
  are the product.
- **Tests.** Every test file sets `pytestmark = pytest.mark.unit` or
  `.integration`. Async tests are driven with `helpers.run(...)`; pytest-asyncio
  is deliberately **not** a dependency, so do not add `async def test_*`.
- **Docstrings** explain *why* a rule exists, not what the next line does. Cite
  the PRD section (`PRD 02 §5.6`) when a behaviour looks surprising.
- **No HTTP vocabulary.** The boundary is a naming rule, not an aspiration: no
  `Request`, `Response`, headers, cookies, bodies, status codes, routes,
  controllers, extractors or ASGI scopes in code, in `__all__`, or in a docstring
  that would teach a reader to expect one. `tests/unit/test_non_goals.py`
  enforces the name-level half; the rest is review.
- **Parity with the framework.** `lauren_di` is the standalone extraction of
  `lauren/_di`. Shared behaviour must stay identical; a deliberate divergence is
  recorded in PRD 08's matrix with a written justification before it ships.
  Review against `../lauren-framework`; `tests/parity/` is the automated gate.
- **Standard library only.** `dependencies = []` stays empty (§1); the extras are
  contributor-only. A patch that needs a third-party import is out of scope.
- **New public symbol?** In one commit: add it to `lauren_di.__all__`, add a
  `### Symbol` section to `docs/reference.md` (by regenerating it), regenerate
  `llms-full.txt`, then run `nox -s llms_check docs_contract docs`.

## 6. Generated files — edit the source, not the output

| Generated | Source | Regenerate |
|---|---|---|
| `llms-full.txt` | `docs/reference.md` (`### Symbol` sections) | `python scripts/build_llms_full.py` |
| `site/` | `docs/` + `mkdocs.yml` | `nox -s docs` |
| `dist/*`, `build/` | `lauren_di/`, `pyproject.toml` | `nox -s build` |

`llms-full.txt` is checked, not trusted: `scripts/check_llms_full.py` fails when
a name in `__all__` has no `## Name` section or that section has no fenced
python block, and when the `llms.txt` index stops naming a symbol or points at a
page that does not exist. The index's links are read by
`scripts/check_docs_contract.py`'s resolver, so `nox -s llms_check` and
`nox -s docs_contract` cannot disagree about what a link is.

`.gitignore` ignores the outputs that are pure artefacts (`site/`, `dist/`,
`build/`, the caches, `.nox/`) and deliberately keeps `docs/reference.md` and
`llms-full.txt` tracked, because both are reviewed in the diff and verified by
CI. `tests/unit/test_gitignore_contract.py` asserts both halves, and cross-checks
its own matcher against real `git check-ignore`.

## 7. Definition of done

```bash
nox -s ci        # lint, format, typecheck, tests 3.10–3.14, coverage, llms_check,
                 # docs_contract, prds, docs, build, build_check
```

If a session fails, fix the cause. **Never weaken a gate to get green** — do not
lower `fail_under`, delete an assertion, add a `# noqa`, or narrow a session to
the tests that pass. Those changes are indistinguishable from progress in a diff
and are the one thing this repository will not forgive.

## 8. Release

`CHANGELOG.md` follows Keep a Changelog and the project follows SemVer. Adding an
entry under `## [Unreleased]` is part of any user-visible change. Publishing is
tag-driven: push a `v*.*.*` tag and `.github/workflows/release.yml` builds,
validates and publishes via PyPI trusted publishing. `nox -s release_test`
performs the same build/validate/install steps locally without credentials.
