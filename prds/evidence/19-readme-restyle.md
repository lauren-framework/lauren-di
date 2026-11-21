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
