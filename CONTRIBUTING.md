# Contributing to lauren-di

`lauren-di` is a small library with a deliberately narrow scope, so most of the work
in a contribution is deciding *whether* it belongs here. This page is that decision
procedure plus the mechanics of landing a change.

## Before you start

The library is a standalone dependency-injection container: explicit registration,
typed scopes, lifecycle hooks, module visibility. Standard library only, Python 3.10+.
It never imports `lauren`, and nothing about it requires `lauren` to import it.
`docs/index.md` states the whole contract on one page.

Two rules are absolute. A pull request that breaks either one is not negotiated:

| Rule | What it means here |
| --- | --- |
| **No runtime dependencies** | `dependencies = []` in `pyproject.toml` stays empty. Everything under `[project.optional-dependencies]` is contributor-only: the `dev` extra is test and type-check tooling, not something an application installs. |
| **Never weaken a gate** | Green means the gate passed exactly as written. No lowered `fail_under`, no deleted assertion, no blanket `# noqa`, no narrowed `mypy` scope, no skipped docs check. |

### Set up

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install nox
nox -s tests
```

`nox` builds its own virtualenvs from `noxfile.py`, so the editable install is only
there for your editor and for running a script directly. Two shortcuts that skip the
matrix entirely:

```bash
python scripts/check_docs_contract.py                     # the docs contract, no nox
python -m pytest tests/docs/test_depends_spelling.py -q   # the `Depends[X]` spelling rule
```

Read the PRD you are changing in `prds/` before you write code, and check
`../lauren-framework` whenever the change touches shared behaviour: the two halves of
the framework must agree, and a change that only lands here is half a change.

## The boundary test: "is this DI?"

PRD 01 §3 is normative: the non-goals are **out of scope**, not "not yet done". A
patch that adds one of these should be redirected to a different library rather than
merged here.

Ask, in order:

1. **Does it name HTTP?** `Request`, `Response`, `Headers`, `QueryParams`, cookies,
   bodies, media types, status codes, ASGI scopes — no. Runtime-supplied values
   arrive as an opaque `values` mapping; the container never learns what a request is.
2. **Does it name routing or transport?** `@controller`, route registration, path
   templates, converters, routers, mounts, URL generation, versioning — no.
3. **Does it name extraction?** `Path`, `Query`, `Header`, `Body`, `Form`, `Bytes`,
   `UploadFile`, `ExtractionMarker` — no. `Depends[T]` survives only as a DI
   declaration marker.
4. **Does it name scheduling or an application object?** `@scheduled`, cron parsing,
   hosted services, worker supervision, `LifecycleScheduler` — no. Startup and
   shutdown are container methods.
5. **Does it name a module graph?** `@module`, `imports`/`exports`/`controllers`, a
   transitive visibility graph, module-level override substitution — no. *Declared*
   visibility sets are a container primitive; a graph compiler is not.
6. **Does it name configuration, a CLI, or a schema layer?** env/file/secret sources,
   coercion, typed settings, console scripts, OpenAPI, pydantic/msgspec integration,
   body codecs, pagination, response contracts — no. Config values are registered as
   ordinary providers, nothing more.
7. **Does it add a store, session, event bus, background job, health probe, logging
   configuration, static files, WebSocket or SSE support?** No — those *consume* DI.
8. **Does it add a runtime dependency?** No. `dependencies` in `pyproject.toml` stays
   empty; the extras are contributor-only.
9. **Does it add sync resolution?** No in v1: `await` is the only resolution API.
10. **Does it inject without a declaration?** No. A class must be marked
    `@injectable()` or registered through `use_class`; the container never scans
    imports.

If the answer is "yes" to any of those, the feature belongs in the layer above
(`lauren` or the application), not here. If it is a DI concept — a provider shape, a
scope rule, a validation error, an override mechanism — it belongs here, and it needs
a test.

## The parity requirement

`lauren_di` is the standalone extraction of `lauren/_di`. Behaviour that the two
share must stay identical: a divergence is either a bug here or a documented,
justified improvement recorded in PRD 08's matrix. When you change a shared
behaviour, check it against the framework checkout (`../lauren-framework`) before
arguing about it in review.

## The gates

Every gate runs through `nox`, which is also exactly what CI runs, so a green local
run means a green pipeline.

```bash
nox                     # fast default set: lint, unit, integration, typecheck
nox -s ci               # everything CI runs
nox -s tests            # the whole suite on every supported interpreter
```

The two documentation gates are the ones a docs change must run:

```bash
nox -s docs_contract    # nav completeness both ways, every python block compiles,
                        # `run` blocks execute, every __all__ name has an API section
nox -s llms_check       # every public symbol is documented in llms-full.txt
```

`docs_contract` is the reason documentation here is not prose-only: a code block that
does not compile, a page missing from `mkdocs.yml`, an orphaned page, or a public name
without a reference section is a **test failure**, not a review comment.

Other gates worth knowing:

| Session | What it enforces |
|---------|------------------|
| `nox -s lint` / `format` | ruff, including `RUF022`: `__all__` stays sorted |
| `nox -s typecheck` / `typing` | `mypy --strict` and `pyright` strict over the public surface |
| `nox -s coverage` | branch coverage against `fail_under` in `pyproject.toml` |
| `nox -s prds` | `prds/check_consistency.py`: index parity, the ten-section template, every relative link, every `PRD 0N §x` cross-reference, the decision register, symbol/default/scope agreement, the no-web-framework rule, and the baseline's owner references |
| `nox -s tests` | the whole suite on every supported interpreter in one pass |
| `nox -s docs` | `mkdocs build --strict`: a broken link or a page missing from `nav` fails the build |
| `nox -s docs_contract` | `scripts/check_docs_contract.py`: `nav` completeness in both directions, every relative link, every fenced `python` block compiles, every `run` block executes, every `__all__` name has an API section, `docs/reference.md` is current |
| `nox -s llms_check` | `scripts/check_llms_full.py`: `llms-full.txt` and the `llms.txt` index still match the public surface |
| `nox -s parity` | the same behaviour through `lauren`, or the library half alone with `--allow-missing-reference` |
| `nox -s build build_check` | the wheel builds, `twine check --strict` passes |

**Never weaken a gate to get green.** No lowering `fail_under`, no deleting or
`xfail`-ing an assertion, no blanket `# noqa`, no narrowing a session to the tests
that happen to pass. A hidden check and a fixed check look the same in a diff, so
this rule is absolute.

## Layout

| Path | What lives there |
|------|------------------|
| `lauren_di/` | the package; `__all__` in `__init__.py` is the public surface |
| `tests/unit/`, `tests/integration/` | behaviour |
| `tests/packaging/` | the built wheel: contents, metadata, import graph |
| `tests/docs/` | the PRD's own examples |
| `tests/typing/` | `reveal_type` fixtures for the type checkers |
| `docs/` | the site: `index.md`, `getting-started.md`, `guides/`, `reference.md` |
| `prds/` | the specification this library is written against |
| `.gitignore` | the artefacts `nox` creates; `tests/unit/test_gitignore_contract.py` checks both directions |

## Documentation changes

The documentation is read by humans *and* by coding agents as ground truth, so a code
block that does not run is a bug. The corpus:

| Page | Answers |
| --- | --- |
| `docs/index.md` | what the library is and is not |
| `docs/getting-started.md` | install, first container, first provider, first scope |
| `docs/guides/providers.md` | registration forms, factories, `Depends[X]` |
| `docs/guides/scopes.md` | the scope ladder, and when to pick one |
| `docs/guides/custom-providers.md` | writing a provider that is not a plain class |
| `docs/guides/testing.md` | overrides, isolation, what to assert |
| `docs/recipes.md` | task-shaped examples, one per page section |
| `docs/visibility.md` | what a module exports to its importers |
| `docs/errors.md` | the error catalogue: class, code, cause |
| `docs/troubleshooting.md` | symptom, cause, fix |
| `docs/concurrency.md` | thread and task safety of resolution |
| `docs/design.md` | why the container is shaped this way |
| `docs/migration.md` | moving off another container |
| `docs/reference.md` | the API reference, generated from the source |

Rules for a documentation change:

- **Generated pages are never hand-edited.** `docs/reference.md` comes from
  `scripts/build_reference.py` and `llms-full.txt` from `scripts/build_llms_full.py`.
  Regenerate both and let the diff ride along in the same commit.
- **Add the page to `nav` in `mkdocs.yml` in the same commit.** `nox -s docs_contract`
  checks completeness in both directions, so an orphan page and a dead nav entry both
  fail.
- **Every fenced `python` block must compile on its own.** A block tagged `run` is
  executed, so it must also be correct at runtime, with no imports from `lauren`.
- **Relative links resolve from the repository root**: `docs/recipes.md`,
  `prds/01-product-definition-and-public-api.md`, `noxfile.py`.
- **The quickstart is one block, quoted byte-for-byte.** The same text lives in
  `README.md`, `docs/getting-started.md` and `tests/integration/test_quickstart.py`;
  edit one copy and you edit all three.
- **Use the documented annotation spelling.** `Depends[X]` belongs in annotation
  position; `tests/docs/test_depends_spelling.py` fails on the parameter form that
  looks like it but is not.

## Adding a public symbol

One symbol means five edits, all in the same commit:

1. Implement it in the private module under `lauren_di/` that owns the concept.
2. Export it from `lauren_di/__init__.py` and add it to `__all__` in sorted order
   (`RUF022` enforces the sort).
3. Give it a docstring: the first line becomes the summary in the API reference.
4. Regenerate the derived artefacts: `python scripts/build_reference.py`, then
   `python scripts/build_llms_full.py`.
5. Add the symbol to the table in `prds/01-product-definition-and-public-api.md`.

Then run `nox -s lint typecheck docs_contract llms_check` before you push. A public
name with no API section fails `docs_contract`, not review.

## Adding an error code

An error is one class, one code string, two doc rows and a PRD row:

1. Subclass `DIError` in `lauren_di/_errors.py` with a lowercase snake-case `code`
   (`missing_provider`, `circular_dependency`) and a docstring stating when it is
   raised. Export it and regenerate the reference as above.
2. Add a row to the catalogue in `docs/errors.md`: class, `code`, cause.
3. Add a row to `docs/troubleshooting.md`: symptom, cause, fix, written for someone
   holding a traceback and no theory.
4. Record the code in the error model, PRD 05.
5. Test the `code`, not only the message: the code is the contract.

## Review checklist

- [ ] All ten boundary questions answer no.
- [ ] `dependencies = []` is still empty and nothing under `lauren_di/` imports
      outside the standard library.
- [ ] `nox -s ci` is green, and no gate was relaxed to get there.
- [ ] New public names are in `__all__`, sorted, and in the PRD symbol table.
- [ ] `docs/reference.md` and `llms-full.txt` are regenerated, never hand-edited.
- [ ] New pages are in `nav`; every relative link resolves from the repository root.
- [ ] A new error code has its `docs/errors.md` row, `docs/troubleshooting.md` row and
      PRD 05 row.
- [ ] Parity with `../lauren-framework` checked whenever behaviour changed.

## Licence

By contributing you agree your contribution is licensed under the MIT licence that
covers this repository.
