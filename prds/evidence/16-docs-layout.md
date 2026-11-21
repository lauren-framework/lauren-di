# Goal #16 — documentation corpus moved to the PRD 01 §9 layout

§9 names the documentation artefacts by *address*, not only by content:

| §9 artefact | Before | After |
|-------------|--------|-------|
| `docs/getting-started.md` | `docs/quickstart.md` | **created** (`git mv`, then rewritten) |
| `docs/guides/providers.md` | — (PRD 02 content was spread over `custom-providers.md`) | **new page** |
| `docs/guides/scopes.md` | `docs/scopes-and-lifecycle.md` | moved |
| `docs/guides/custom-providers.md` | `docs/custom-providers.md` | moved |
| `docs/guides/testing.md` | `docs/overrides.md` | moved |
| `CONTRIBUTING.md` | missing (§9 names it) | **new file** |

`docs/index.md`, `docs/visibility.md`, `docs/errors.md`, `docs/migration.md` and the API
reference keep their addresses; §9 does not name them.

## The moves are content-preserving, the addresses were not

B-6 in `01-conformance-baseline.md` recorded that the old names were hard-coded in three
places. All three moved with the files:

- `mkdocs.yml` `nav:` — now `Home / Getting started / Guides{providers, scopes,
  custom-providers, testing} / Module visibility / Errors / Migration / API reference`.
  `docs_contract`'s nav-vs-disk check passes in **both** directions (no nav entry without a
  file, no file without a nav entry), so the rename did not leave an unreachable page.
- `llms.txt:44-55` — every `docs/…` link now names a path that exists.
- `scripts/check_docs_contract.py`'s coverage check still reads the API reference page;
  that file was not renamed (see "Open item" below).

Cross-references retargeted: `README.md` Documentation, `docs/index.md` "Where to go next",
`.github/ISSUE_TEMPLATE/documentation.yml`, the `Next` sections of the moved pages, and the
new `CONTRIBUTING.md`.

```console
$ python - <<'PY'   # every relative markdown link in README, CONTRIBUTING and docs/
checked 19 relative links in 12 files
no broken relative links
$ python - <<'PY'   # same check over llms.txt, llms-full.txt, AGENTS.md, CLAUDE.md, CHANGELOG.md
checked 10
no broken relative links
```

## `docs/getting-started.md` now *is* §5.2, not a near-miss

§9 says the README carries "§5.2 quickstart verbatim", and
`tests/integration/test_quickstart.py` discovers every documentation file that quotes the
snippet and requires the block to be byte-identical. The first draft of the page taught a
`Repo`/`Service` program instead — a different program under the same name — and the guard
failed exactly as designed:

```console
FAILED tests/integration/test_quickstart.py::test_getting_started_carries_the_snippet_once_the_page_exists
E   assert QUICKSTART in _python_blocks(GETTING_STARTED)
E   AssertionError: docs/getting-started.md quotes the quickstart but its block differs from PRD 01 §5.2
```

The page was restructured around the normative block instead of beside it: Install →
Quickstart (the §5.2 block, lifted mechanically out of the PRD so it cannot drift) →
"What the snippet shows" (declaration, registration, validation, resolution and scopes,
each an excerpt of the block above) → Zero configuration → Next.

The block is marked for execution with the HTML comment form the contract prefers on
`docs/` pages, so the published page runs in `nox -s docs_contract`:

```markdown
<!-- docs-contract: run -->
```python
```

## A second page was a false positive, and the guard was right to fire

`docs/guides/providers.md` carries the function-provider example with `class UserRepo:` and
its end-to-end block imported `from lauren_di import DIContainer, Scope, injectable`. Both
are the quickstart's markers, so the discovery step classified the page as a copy and then
demanded the verbatim block:

```console
FAILED tests/integration/test_quickstart.py::test_every_documented_copy_is_byte_identical[providers.md]
E   AssertionError: docs/guides/providers.md quotes the quickstart but its block differs from PRD 01 §5.2
```

`Scope` was unused in that block, so the import was corrected to
`from lauren_di import DIContainer, injectable`. That is a real cleanup, not a
test-appeasing edit: the page's program uses no scope, and a reader who copied the import
would have carried an unused name. The `UserRepo` example above it is genuine PRD 02
material (a function provider bound with `Depends[make_session]`) and stays.

## Verification

```console
$ nox -s docs_contract llms_check
nox > python scripts/check_docs_contract.py
OK: 13 documentation file(s) satisfy the contract
nox > python scripts/check_llms_full.py
OK: all 38 public symbols of `lauren_di` are documented in llms-full.txt
nox > Ran 2 sessions in 8 seconds:
nox > * docs_contract: success, took 4 seconds
nox > * llms_check: success, took 4 seconds

$ nox -s docs
INFO    -  Documentation built in 1.26 seconds
nox > Session docs was successful in 2 seconds.

$ python -m pytest tests -q
302 passed in 13.57s

$ python -m pytest tests/integration/test_quickstart.py -q
13 passed in 0.56s
```

`docs_contract`'s 13 files are `README.md`, `llms.txt`, `llms-full.txt` and the 10 pages
under `docs/`; every `python` block compiles and the two `run`-marked blocks execute
(`getting-started.md`, `guides/providers.md`).

## Open item, deliberately not closed here

§9's table names `docs/reference.md`; the API reference page is `docs/api.md`, and
`check_docs_contract.py`'s coverage check is hard-wired to that path. This is B-8
("no reference generator, no freshness gate"), not B-6: the file that exists is complete and
enforced, and renaming it without the generator §9 also asks for would trade a working page
for a §9-shaped name. B-6 is about addresses of pages that had no enforcement at all; B-8 is
about a page that must be *generated*. Recorded so the divergence is a decision, not an
oversight.
