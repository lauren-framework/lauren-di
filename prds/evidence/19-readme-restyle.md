# Goal 19 — README restyle

`README.md` now reads like `../lauren-framework/README.md`: a centred `<h1>`
hero, a one-sentence `<em>` tagline, a badge row, an
`## For AI Agents & Coding Assistants` resource table, then the existing
intro / install / quickstart / features material, unchanged in substance.

## What changed

| File | Change |
|---|---|
| `README.md` | Restyled; 97 → 159 lines. Quickstart block byte-identical (see below). |
| `tests/unit/test_readme_contract.py` | New — 25 tests that turn every README claim into an assertion. |
| `mkdocs.yml` | `site_url` → `https://lauren-framework.github.io/lauren-di/`. |
| `pyproject.toml` | `[project.urls] Documentation` → the same GitHub Pages URL. |
| `.github/ISSUE_TEMPLATE/config.yml` | Documentation contact link → the same URL. |

The `site_url` / `Documentation` moves are part of this goal, not a side quest:
`.github/workflows/docs.yml` already builds `site/` and deploys it with
`actions/deploy-pages@v4`, so GitHub Pages **is** the address that serves these
pages. Leaving `lauren-py.dev/lauren-di/` in `mkdocs.yml` while the README
linked to Pages would have produced two addresses for one site, one of which
404s.

## Badge row (8 badges, each checked against a file in this repo)

| Badge | Target | Derived from |
|---|---|---|
| Tests | `.github/workflows/tests.yml` | the workflow file |
| Lint | `.github/workflows/lint.yml` | the workflow file |
| CodeQL | `.github/workflows/codeql.yml` | the workflow file |
| PyPI package | `lauren-di` | `project.name` |
| Supported Python versions | `lauren-di` | `project.name` |
| License: MIT | `LICENSE` | `LICENSE` + `project.license` |
| Ruff | `astral-sh/ruff` badge endpoint | static |
| Checked with mypy | static `types-mypy` | static |

Deliberately **not** copied from the framework: Codecov (no coverage service
configured here), `prek` / pre-commit (no hook installed here), Discussions,
stars and download counts (no published repository metrics). Each is a named
entry in `SERVICES_NOT_RUN` and has its own parametrised test, so re-adding one
fails with the service's name in the message.

## Guarantees preserved

| Guarantee | Evidence |
|---|---|
| §5.2 quickstart byte-identical to PRD 01 §5.2 and `docs/getting-started.md` | `pytest tests/integration/test_quickstart.py -q` → **13 passed** |
| Every fenced python block still compiles | `python scripts/check_docs_contract.py` → `OK: 13 documentation file(s)` |
| Relative links resolve on disk | `test_every_relative_link_resolves_on_disk` |
| `llms.txt` / `llms-full.txt` still consistent | `python scripts/check_llms_full.py` → both OK (38 symbols) |
| PRD set still consistent | `python prds/check_consistency.py` → `all checks passed` |
| Lint / format | `ruff check lauren_di tests scripts noxfile.py` → all passed; `ruff format --check` → 42 files already formatted |
| Unit + integration suite | `pytest tests/unit tests/integration -q` → **317 passed** |

Quickstart byte-identity was checked directly, before and after the rewrite:

```
python blocks in README.md: 1
identical to PRD 5.2: True
bytes: 781
```

The restyle was applied by substituting the *extracted* block back into the new
template, so the block could not be retyped by accident.

## Live resolution of every absolute target (checked 2026-02-xx, `curl -L`)

| Target | Status |
|---|---|
| `https://img.shields.io/pypi/v/lauren-di…` | 200 (renders “package or version not found”) |
| `https://img.shields.io/pypi/pyversions/lauren-di.svg…` | 200 (same) |
| `https://img.shields.io/badge/license-MIT-blue.svg` | 200 |
| `https://img.shields.io/endpoint?url=…ruff…/badge/v2.json` | 200 |
| `https://img.shields.io/badge/types-mypy-blue.svg` | 200 |
| `https://github.com/astral-sh/ruff` | 200 |
| `https://mypy.readthedocs.io/en/stable/` | 200 |
| `https://lauren-py.dev` (the framework) | 200 |
| `https://pypi.org/project/lauren-di` | 200 (bot challenge page; PyPI JSON API still 404 → not published) |
| `https://github.com/lauren-framework/lauren-di` | **404 — repository not created yet** |
| `https://github.com/lauren-framework/lauren-di/actions/workflows/{tests,lint,codeql}.yml/badge.svg` | **404 for the same reason** |
| `https://github.com/lauren-framework/lauren-di/blob/main/LICENSE` | **404 for the same reason** |
| `https://lauren-framework.github.io/lauren-di/` | **404 — Pages deploy has not run yet** |

The four 404s are all one cause: the GitHub repository named by
`pyproject.toml` (`Repository`, `Homepage`, `mkdocs.yml:repo_url`) does not
exist yet. Nothing in the README can resolve them before the repository is
created and the first `docs` workflow run publishes Pages; the badge and link
targets are the ones the repository's own metadata declares, so they resolve as
soon as the repository does. This is recorded rather than papered over: the
goal's “returns 200” clause cannot be satisfied for a repository that has not
been pushed, and the alternative — badges that resolve to something else —
would be exactly the false claim this goal forbids.

## The gate that stays behind

`tests/unit/test_readme_contract.py` (25 tests) asserts, offline:

1. the hero block is `<div align="center">` + `<h1><i>lauren-di</i></h1>`, and
   the tagline is one `<em>` sentence;
2. the badge row is exactly the eight documented badges, in order, each with a
   link and alt text;
3. no badge names any of ten services this repository does not run;
4. every workflow badge names a file in `.github/workflows/` **and** the
   repository slug from `pyproject.toml`;
5. every PyPI badge queries `project.name`;
6. the licence badge agrees with `LICENSE` and `project.license`;
7. every relative link resolves on disk;
8. every absolute link is on a host allow-list, and `github.com` links point at
   this repository or a tool the badges already name;
9. the retired `lauren-py.dev/lauren-di` address is gone;
10. `mkdocs.yml`'s `site_url` is the Pages address and the README names it;
11. the agent table links all four §9 artefacts and each file exists, above the
    prose intro.

Two of these were drilled by hand while writing the file: adding
`codecov` to the badge row fails `test_no_badge_claims_a_service_this_repository_does_not_run[codecov]`,
and pointing a workflow badge at `lauren-framework/lauren-framework` fails
`test_workflow_badges_name_workflows_that_exist_here` with the mismatched slug
in the message.

## After publication — the four 404s are now 200 (checked `curl -L`, goal 32)

The repository was created and pushed (19 commits on `main`, tracking
`origin/main`), and `.github/workflows/docs.yml` has deployed Pages. Re-checking
the four targets the table above recorded as 404:

| Target | Before | After |
|---|---|---|
| `https://github.com/lauren-framework/lauren-di` | 404 | **200** |
| `…/actions/workflows/tests.yml/badge.svg?branch=main` | 404 | **200** |
| `…/actions/workflows/lint.yml/badge.svg?branch=main` | 404 | **200** |
| `…/actions/workflows/codeql.yml/badge.svg?branch=main` | 404 | **200** |
| `…/blob/main/LICENSE` | 404 | **200** |
| `https://lauren-framework.github.io/lauren-di/` | 404 | **200** |
| `https://pypi.org/project/lauren-di/` | 404 (bot page) | **200** |

So the clause the earlier section called unsatisfiable ("returns 200") now holds
for all four, and the note that recorded it as unsatisfiable is left in place
rather than rewritten — it was true when it was written, and the after-state is
the evidence that the cause it named (a repository that did not exist) was the
whole cause.

## Publish audit — what ships and what does not

The pre-flight audit of the first commit's contents produced **two opposite
decisions**, and the difference matters because both directories look like
scratch:

| Path | Decision | Why |
|---|---|---|
| `tests/.cassette/001c32cb-…/` | **ignored + untracked** | `cassette.jsonl` is a verbatim agent-session transcript and `approvals.jsonl` its tool-approval log. Nothing in the repository reads the directory; `grep -rn cassette` outside it finds only unrelated `rich` vendored emoji codes. It is not a fixture, and it dirtied the tree on every session. |
| `prds/evidence/**` (25 `.py` probes, 11 `.md`, 24 recorded transcripts) | **tracked, deliberately** | This is not throwaway scratch. PRDs 02–08 cite the `E-nnn` ids these probes print *in place of prose assertions*, and `prds/evidence/run_all.py` records every transcript and **fails when a cited id is emitted by no probe** (PRD 06 §6; GOALS.md ground rule 2 also points at it for the `PYTHONPATH` pin). Deleting or ignoring it would break the citation chain the PRDs rest on. |

The goal text described `prds/evidence/**` as "throwaway probe scripts that
`ruff check .` rejects (317 errors)". The error count is accurate but the
inference is not: **316 of those 317 errors are in `prds/evidence/` simply
because that directory is outside the `lint` session's targets**
(`lauren_di tests scripts noxfile.py`), so `ruff check .` measures a scope the
gate never claims. The one remaining error is `prds/check_consistency.py`, which
goal 36 owns. A lint-scope mismatch is not evidence that cited evidence should
be deleted, so the files stay and the `.gitignore` records why.

`tests/unit/test_gitignore_contract.py` now enforces both decisions in both
directions, so neither can silently flip:

* `tests/.cassette/…` is in `ARTEFACTS` (must be ignored) **and**
  `test_the_cassette_is_ignored_and_untracked` asserts `git ls-files` returns
  nothing for it;
* `EVIDENCE_BASE` (six representative probe/transcript paths) must exist, must
  **not** be ignored, and must be **in the index** — `git rm --cached
  prds/evidence/run_all.py` fails the suite.

Drilled both ways: re-adding `tests/.cassette/x/cassette.jsonl` with
`git add -f` fails with `an agent session recording is committed: […]`;
`git rm --cached prds/evidence/run_all.py` fails with `the evidence base is not
fully tracked; missing from the index: ['prds/evidence/run_all.py']`. Both
reverted.

### The documented test count moved, and the goal-31 gate caught it

This goal added nine tests to `tests/unit/test_gitignore_contract.py` (six
parametrised evidence-base cases plus three decision tests), which changed the
collected total under AGENTS.md's feet. `nox -s ci` failed on **all five**
interpreters in `tests/unit/test_documented_test_count.py` with

```text
E  AssertionError: AGENTS.md's quick start implies 451 tests on Python 3.13,
   but `pytest -q --ignore=tests/parity` collects 460.
```

which is the gate from goal 31 doing exactly what it was built for: the number
is a checked claim, so it could not drift silently when the suite grew. The line
is now

```text
pytest -q   # full suite: 460 tests (435 on 3.10); +52 parity with ../lauren-framework
```

reconciled against measurement rather than arithmetic: base 460 (`--ignore=tests/parity`),
full 512 (460 + 52 parity), and 460 − 435 = 25 = the size of
`tests/unit/test_readme_contract.py`, the module that does not collect on 3.10
because `tomllib` is stdlib only from 3.11. `prds/evidence/31-documented-test-count.md`
keeps the 451/426 figures it recorded at the time, since it is a dated record of
that goal; this note is the pointer that they have since moved.
