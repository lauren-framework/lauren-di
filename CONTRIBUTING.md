# Contributing to lauren-di

`lauren-di` is a small library with a deliberately narrow scope, so most of the work
in a contribution is deciding *whether* it belongs here. This page is that decision
procedure plus the gates a patch has to pass.

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

The §9 layout is:

```
docs/index.md              what the library is and is not
docs/getting-started.md    install, first container, first provider, first scope
docs/guides/providers.md   constructor, field and function providers
docs/guides/scopes.md      scope matrix, strict mode, teardown
docs/guides/custom-providers.md   the four recipes and list[T]
docs/guides/testing.md     overrides and fixtures
docs/reference.md          every exported name, generated from `__all__`
```

When you add a page, add it to `mkdocs.yml`'s `nav` in the same change — an orphaned
page fails `docs_contract`. When you rename one, update the nav, `README.md`,
`llms.txt`, the issue template's file list and every in-page cross-reference in the
same change.

## Licence

By contributing you agree your contribution is licensed under the MIT licence that
covers this repository.
