# Goal #36 — gate `prds/check_consistency.py`

## The defect

`prds/check_consistency.py` implements eight checks over the PRD corpus, one of
which (`owners`) exists precisely to stop a baseline table from pointing a reader
at the wrong goal. Nothing ran it: no nox session, no CI job, no test. The
command in its own docstring was the only runner, so a silent off-by-one in
`prds/evidence/01-conformance-baseline.md` survived until a human happened to
type it. A check that runs only when someone remembers it is documentation, not a
gate — the same failure mode `tests/unit/test_noxfile_contract.py` was written
for, one level up.

## What changed

| File | Change |
| --- | --- |
| `noxfile.py` | new `prds` session (runs `prds/check_consistency.py`), listed in `nox -s ci`; `PRDS_DIR`/`PRDS_CHECK` constants; one `RUFF_TARGETS` tuple shared by `lint` and `format`, now covering `prds` |
| `.github/workflows/lint.yml` | new `prds` job running `nox -s prds` on 3.14 |
| `pyproject.toml` | `prds` added to `[tool.ruff] src`; `prds/evidence` added to `extend-exclude` with the reason |
| `prds/check_consistency.py` | `B905` fixed (`zip(..., strict=False)`, with the reason); reformatted by `ruff format`; docstring names the gate |
| `tests/unit/test_noxfile_contract.py` | five tests: the session runs the script, it appends posargs, it is wired into *both* the aggregator and a CI job, the ruff targets cover the checker but not the evidence, and `lint`/`format` share one target tuple |
| `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` | the session is documented; `AGENTS.md`'s checked test count moved 511 → 516 (485 → 490 on 3.10) for the five new tests |

## Why the ruff target is `prds` and not `prds/evidence`

`ruff check .` reports 317 errors; 316 of them are in `prds/evidence/`. Those
files are **cited evidence**, not scratch: PRDs 02-08 quote the `E-nnn` ids they
emit and `prds/evidence/run_all.py` fails when a cited id is emitted by no probe,
so reformatting one would invalidate the citation chain.
`tests/unit/test_gitignore_contract.py::EVIDENCE_BASE` asserts the same files stay
committed. The exclusion is therefore one directory wide —
`[tool.ruff] extend-exclude = [..., "prds/evidence"]` — so `prds/check_consistency.py`
is linted and formatted like any other code while the probes stay byte-identical.
`git diff --stat prds/evidence` is empty after this change, and
`test_the_ruff_targets_cover_the_prd_checker_but_not_its_evidence` fails if the
exclusion is dropped or the target widens to the evidence directory.

## Verification

```
$ python prds/check_consistency.py
  ok   index: 8 PRDs on disk, 8 rows in README §2
  ok   template: 8 PRDs × 10 sections checked
  ok   links: every relative markdown link resolves
  ok   cross-refs: 127 'PRD 0N §x' references checked
  ok   decisions: D-1…D-56 defined, 56 rows
  ok   symbols: shutdown/Depends/surface/resolve/overrides/scopes/Provider all agree
  ok   scope: no web-framework imports; unguarded `import lauren` samples: 0
  ok   owners: 26 baseline owner reference(s) resolve to the goal that closed them

all checks passed                                    (exit 0, eight ok lines)

$ nox -s prds                                        Session prds was successful.

$ ruff check prds            -> All checks passed!
$ ruff format --check prds   -> 1 file already formatted
$ git diff --stat prds/evidence -> (empty: the probes are untouched)
```

### Drill A — the session cannot be dropped from the aggregator

Deleting `("prds", [])` from `nox -s ci`'s plan:

```
FAILED tests/unit/test_noxfile_contract.py::test_no_defined_session_escapes_the_aggregator
FAILED tests/unit/test_noxfile_contract.py::test_workflow_only_sessions_are_the_declared_exceptions
FAILED tests/unit/test_noxfile_contract.py::test_the_prds_gate_is_wired_into_both_the_aggregator_and_ci
3 failed, 28 passed
```

### Drill B — the session cannot be dropped from CI

Removing the `prds:` job from `.github/workflows/lint.yml`:

```
FAILED tests/unit/test_noxfile_contract.py::test_every_session_the_aggregator_runs_has_a_ci_job
FAILED tests/unit/test_noxfile_contract.py::test_the_prds_gate_is_wired_into_both_the_aggregator_and_ci
2 failed, 29 passed
```

### Drill C — a wrong owner reference fails the session

The `owners` check is what the gate exists for, so the gate was proved against
exactly the defect it was written for. `B-2`'s owner was changed from `#4`
(goal #4, the `resolve` signature correction) to `#7`:

```
$ nox -s prds
  FAIL [owners] B-2: #7 is described as '`test_quickstart.py`,
       `test_prd_examples.py`, `test_non_goals.py`' in GOALS.md, which does
       not contain 'signature'
1 failure(s)
nox > Session prds failed.                          (exit 1)
```

Restored, `nox -s prds` is green again. Repeating the drill with `B-4`'s owner
`#15` → `#14` (the actual off-by-one class) fails identically:
`FAIL [owners] B-4: #14 is described as 'Python 3.14 in classifier /
SUPPORTED_PYTHONS / CI matrix' in GOALS.md, which does not contain 'extras'`.

## Result

`nox -s ci` is green end to end with `prds` in the plan, and the corpus checker
now runs on every push instead of on request.

## Amended by goal #37 — the target is `prds` *with* the exclusion

The goal that closed this one named "`prds/`" as the ruff target and claimed
"`ruff check prds` and `ruff format --check prds` are clean". Read literally, that
claim is unsatisfiable: `prds/evidence/**` holds 316 of the repository's 317
`ruff check .` errors, and those files are cited evidence — PRDs 02-08 quote the
`E-nnn` ids they emit, `prds/evidence/run_all.py` fails when a cited id is emitted
by no probe, and `tests/unit/test_gitignore_contract.py::EVIDENCE_BASE` asserts
they stay committed. The target is therefore the directory *plus* one entry:

```toml
[tool.ruff]
extend-exclude = [..., "prds/evidence"]   # one directory wide, not `prds`
```

`extend-exclude` rather than `exclude`, because a plain `exclude` replaces ruff's
default list and `dist/`, `site/` and every virtualenv would start being linted.

That entry was only *declared* here, which is the same gap this goal closed one
level up: a comment cannot tell a one-directory exclusion from a dead one. Goal
#37 makes it a checked claim — `tests/unit/test_noxfile_contract.py` now asks ruff
which files the `prds` target resolves (`--show-files`, with and without
`--force-exclude`), proves the exclusion is load-bearing (an explicitly passed
path bypasses it, so the probes still report 316 errors), and pins its scope to
`prds/evidence/output/ids.json`'s recorded probe list. See
`prds/evidence/37-lint-scope.md`.
