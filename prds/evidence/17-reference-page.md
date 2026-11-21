# Goal 17 — the reference page is generated, and the ignore rules are checked

**Status:** done
**Scope:** `docs/reference.md` (replaces the hand-written `docs/api.md`) and the
repository's first `.gitignore`, with `tests/unit/test_gitignore_contract.py` as
its guard.

Everything below was run in `/root/python_projects/lauren-all/lauren-di`.

---

## 1. `docs/reference.md` is generated from the public surface

`scripts/build_reference.py` renders the page from `lauren_di.__all__` (38 names),
live `inspect.signature`, docstrings, each exception's `code` attribute, `ast`
derived alias definitions, and a hand-written `EXAMPLES` table that
`_validate_examples()` executes at build time. The page header states that it is
generated and that a stale page fails the contract.

| Check | Result |
|---|---|
| `python scripts/build_reference.py` twice in a row | second run is a no-op; `docs/reference.md` sha256 `5a61781f270263b1…` |
| 38 sections, 6 groups | Container and scopes / Declarations / Records and aliases / Custom-provider recipes / Overrides / Errors |
| name removed from `__all__`, contract re-run | fails: the name has no section |
| page hand-edited | `docs_contract` fails: `[reference] page is stale` |
| docstring edited without regenerating | fails stale; after `build_reference.py`, green |

`scripts/check_docs_contract.py` gained `check_reference_freshness()`, and
`scripts/build_llms_full.py` was retargeted from `api.md` to `reference.md` and
now strips `<!-- docs-contract: run -->` directives, so `llms-full.txt`
(sha256 `92ed4f29440cfa42…`) carries 0 markers. Ten files that mentioned
`api.md` were retargeted.

## 2. `.gitignore` exists, and it is checked in both directions

The repository had no ignore file. `git status` therefore listed every artefact
the task runner creates, and the first `git add .` would have committed `dist/`
and the rendered `site/`.

`.gitignore` (79 lines, 43 rules, sha256 `6b8f1ac82437dd6d…`) groups the rules by
origin — byte-compiled code, build products, the docs site, caches, nox
virtualenvs, virtualenvs/env, editors/OS, agent scratch — and states *why* each
group exists. Two deliberate non-ignores are written into the file, because they
are the traps:

* `docs/reference.md` and `llms-full.txt` are generated **and tracked**: they are
  reviewed in the diff and verified by `docs_contract` / `llms_check`.
* `prds/` is the specification (CONTRIBUTING §Layout), not planning scratch —
  unlike `../lauren-framework`, where `prds/` is ignored.

### Verification against the real checkout

A throwaway git directory, pointed at this working tree, was asked what it would
ignore:

```bash
git init -q --bare /tmp/gicheck.git
GIT_DIR=/tmp/gicheck.git GIT_WORK_TREE=$PWD \
  git status --porcelain --ignored=matching -uall
```

| Outcome | Count |
|---|---|
| ignored entries (`!!`) | 19 |
| ignored entries that are not artefacts | 0 |
| files still untracked-and-visible (`??`) | 143 |

The 19 ignored entries are exactly `.agenthicc/`, `.coverage`, `.mypy_cache/`,
`.nox/`, `.pytest_cache/`, `.ruff_cache/`, `coverage.xml`, `dist/`, `site/`,
`lauren_di.egg-info/` and the nine `__pycache__/` directories. Nothing shipped
(`docs/`, `prds/`, `scripts/`, `llms*.txt`, `mkdocs.yml`,
`docs-requirements.txt`) is ignored.

### `tests/unit/test_gitignore_contract.py`

44 tests, 248 lines, sha256 `56694f7a8af9bcd4…`:

* `ARTEFACTS` — 21 artefact paths (realistic children, not bare directories),
  plus three derived from `noxfile.py`'s own `BUILD_DIR` / `DIST_DIR` /
  `SITE_DIR` constants, so a new output directory cannot be added to the task
  runner without also being ignored. All must be ignored.
* `TRACKED` — 40 shipped files, each asserted to exist **and** to not be
  ignored, so the list cannot rot into fiction.
* no rule may be a bare `*` / `**`; no duplicate rules; the file must explain the
  `nox -s clean` relationship and end with a newline.
* `test_matcher_agrees_with_git` runs real `git check-ignore --no-index` in a
  temporary repository over the same paths and requires an identical verdict, so
  the module's matcher cannot pass vacuously. It skips only when `git` is absent.

Drills — each edit made, suite run, file restored, sha256 re-verified:

| Drill | Expected | Observed |
|---|---|---|
| delete the `site/` rule | artefacts test fails | `not ignored by .gitignore: ['site/index.html', 'site/reference/index.html']` |
| add `*.txt` | tracked test fails | 3 failures: `docs-requirements.txt`, `llms-full.txt`, `llms.txt` |
| add `docs/` | tracked test fails | 10 failures, all `docs/` pages |
| drop `!` negation handling from the matcher | cross-check fails, nothing else | `only in matcher: ['.env.example']`, 1 failed / 43 passed |

The last drill is the important one: it isolates
`test_matcher_agrees_with_git` from the other 43 tests, proving the git
cross-check is a real oracle rather than a second copy of the same bug.

## 3. Gates after the change

| Gate | Result |
|---|---|
| `python -m pytest tests -q` | **346 passed** (302 before, +44) |
| `ruff check lauren_di tests scripts noxfile.py` | All checks passed |
| `ruff format --check …` | 40 files already formatted |
| `python scripts/check_docs_contract.py` | OK: 13 documentation file(s) |
| `python scripts/check_llms_full.py` | all 38 public symbols documented |
| `python prds/check_consistency.py` | all checks passed |

## 4. Documentation touch-points

* `AGENTS.md` ground rule 4 now states that `.gitignore` covers artefacts but not
  `docs/reference.md` / `llms-full.txt`, and names the test that enforces it.
* `CLAUDE.md` §6 carries the same rule in the generated-files section.
* `CONTRIBUTING.md` §Layout gained a `.gitignore` row pointing at the guard test.

---

## 5. Verification pass (independent re-run of the goal's acceptance drills)

Run after implementation, on the restored tree.

### Determinism

```bash
python scripts/build_reference.py   # wrote docs/reference.md with 38 sections
sha256sum docs/reference.md          # 5a61781f270263b1…
python scripts/build_reference.py   # wrote docs/reference.md with 38 sections
sha256sum docs/reference.md          # 5a61781f270263b1…   (identical)
python scripts/build_reference.py --check
# OK: docs/reference.md matches lauren_di.__all__ and the docstrings   (exit 0)
```

The page is also stable across interpreter hash seeds, so no set/dict ordering
leaks into the output:

| `PYTHONHASHSEED` | sha256 |
|---|---|
| `0` | `5a61781f270263b1…` |
| `1` | `5a61781f270263b1…` |
| `12345` | `5a61781f270263b1…` |

No timestamp of any kind appears in the page (`grep -Ei '20[0-9]{2}-[0-9]{2}-[0-9]{2}'
'[0-9]{2}:[0-9]{2}:[0-9]{2}'` returns nothing); the header says only that the page
is generated and that a stale page fails the contract.

### Removing a name from `__all__` fails the contract

Both removal drills were run through the real session, `nox -s docs_contract`,
and both exited non-zero:

| Removal | Exit | Message |
|---|---|---|
| `"Owner"` | 1 | `scripts/build_reference.py: [reference] ERROR: Owner has an example but is not exported` |
| `"MissingProviderError"` | 1 | `scripts/build_reference.py: [reference] ERROR: MissingProviderError is in GROUPS but not in lauren_di.__all__` |

The two names fail through different guards — one through `_validate_examples()`,
one through the group/`__all__` reconciliation — so neither path can rot silently.

### Hand-editing the page fails the freshness check

```bash
printf '\nhand edit\n' >> docs/reference.md
python scripts/check_docs_contract.py; echo $?
# docs/reference.md: [reference] page is stale; run `python scripts/build_reference.py`
# 1
```

`check_reference_freshness()` lives at `scripts/check_docs_contract.py:209` and is
called from `main()` at line 303; it re-renders through the generator rather than
comparing against a stored checksum, so a docstring, signature or `code` edit is
caught even when `__all__` is untouched.

### Restored state

| Path | sha256 | Note |
|---|---|---|
| `lauren_di/__init__.py` | `e9dafba997c2b9c1…` | identical to the pre-drill backup; `__all__` back to 38 names |
| `docs/reference.md` | `5a61781f270263b1…` | identical to the committed page; `--check` exits 0 |
| `nox -s docs_contract` | — | `OK: 13 documentation file(s) satisfy the contract` |
| `python -m pytest tests -q` | — | **346 passed** |

### One limitation, recorded honestly

This working directory is not a git repository (`git rev-parse` fails), so the
goal's "commit the generated file" is satisfied only in the sense that the page
exists in the tree and is *tracked by policy*: `docs/reference.md` is one of the
40 entries in `tests/unit/test_gitignore_contract.py::TRACKED`, which asserts it
exists and is **not** ignored. No commit object could be created here; the first
`git add` in a real checkout will pick the file up.
