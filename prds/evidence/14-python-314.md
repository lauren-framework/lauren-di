# Evidence — Python 3.14 declared across the project (goal #14, closes B-5/B-16)

**Date:** recorded at the time of the change.
**Interpreter used:** `/usr/bin/python3.14` → `Python 3.14.4 (main, Aug 20 2026) [GCC 15.2.0]`.

Baseline B-5 recorded that PRD 01 G5 ("CI runs 3.10–3.14") and §5.1's classifier block
promised 3.14 while `pyproject.toml`, `noxfile.py` and `.github/workflows/tests.yml`
stopped at 3.13; B-16 recorded that the gap survived into the built wheel. Both are now
closed, and the three declarations are tied together by a test instead of by review.

## What changed

| Location | Before | After |
| --- | --- | --- |
| `pyproject.toml` classifier | `… :: 3.13` | `… :: 3.13`, `… :: 3.14` |
| `noxfile.py` `SUPPORTED_PYTHONS` | `["3.10", "3.11", "3.12", "3.13"]` | `[…, "3.14"]` |
| `noxfile.py` `PRIMARY_PYTHON` | `"3.13"` | `"3.14"` (newest supported is primary) |
| `.github/workflows/tests.yml` matrix | 4 entries | `["3.10", "3.11", "3.12", "3.13", "3.14"]` |
| `.github/workflows/*.yml` single-version pins | `"3.13"` (10 pins) | `"3.14"`, so every non-matrix job matches `PRIMARY_PYTHON` |
| `README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md`, `noxfile.py` docstrings | `3.10 – 3.13` | `3.10 – 3.14` |

`requires-python = ">=3.10"` is unchanged: 3.14 is the newest *supported* interpreter,
not a raised floor.

## Verification

### The four locations now name 3.14

```console
$ grep -n "3\.14" pyproject.toml
36:    "Programming Language :: Python :: 3.14",

$ grep -n "3\.14" noxfile.py
13:    nox -s tests            # 3.10 – 3.14 matrix
55:SUPPORTED_PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
60:PRIMARY_PYTHON = "3.14"
103:    """Run the whole suite on every supported interpreter (3.10 – 3.14)."""

$ grep -n "3\.14" .github/workflows/tests.yml
27:        python: ["3.10", "3.11", "3.12", "3.13", "3.14"]
45:          python-version: "3.14"
56:          python-version: "3.14"
68:          python-version: "3.14"

$ grep -rn "3\.14" README.md AGENTS.md CLAUDE.md CHANGELOG.md
README.md:86:nox -s tests        # 3.10 – 3.14
AGENTS.md:43:   the 3.10–3.14 matrix, coverage with a fail-under gate, the llms/docs
CLAUDE.md:25:nox -s tests                        # whole suite on 3.10, 3.11, 3.12, 3.13, 3.14
CLAUDE.md:68:- Python 3.10 – 3.14, PEP 561 typed (`lauren_di/py.typed` ships in the wheel).
CLAUDE.md:165:nox -s ci        # lint, format, typecheck, tests 3.10–3.14, coverage, llms_check,
CHANGELOG.md:40:- Standard library only, `py.typed`, Python 3.10 – 3.14.
```

### The whole matrix is green, not just 3.14

```console
$ nox -s tests --python 3.14
296 passed, 1 skipped in 9.84s          # skip: getting-started.md, created by goal #16
nox > Session tests-3.14 was successful

$ nox -s tests --python 3.13
296 passed, 1 skipped
$ nox -s tests --python 3.12
296 passed, 1 skipped
$ nox -s tests --python 3.11
296 passed, 1 skipped
$ nox -s tests --python 3.10
294 passed, 3 skipped                   # +2 skips: tomllib is 3.11+
```

### The single-version gates run on the new primary interpreter

```console
$ nox -s lint format typecheck coverage docs_contract llms_check typing
nox > Session lint was successful
nox > Session format was successful
Success: no issues found in 10 source files        # typecheck (mypy 1.20.2 on 3.14)
nox > Session coverage was successful              # 75% measured, fail_under 71
nox > Session docs_contract was successful
nox > Session llms_check was successful
0 errors, 0 warnings, 75 informations              # typing: pyright 1.1.390 on 3.14
nox > Session typing was successful
```

### The declarations cannot drift apart again

`tests/unit/test_noxfile_contract.py` gained five tests and `tests/packaging/test_wheel.py`
two, all reading the real files rather than a copy of their contents:

| Test | Fails when |
| --- | --- |
| `test_ci_matrix_is_exactly_the_supported_interpreters` | the workflow matrix and `SUPPORTED_PYTHONS` differ (B-5) |
| `test_primary_python_is_the_newest_supported_interpreter` | `PRIMARY_PYTHON` is not the last supported entry |
| `test_every_single_version_ci_job_runs_the_primary_python` | a non-matrix `python-version` pin lags behind `PRIMARY_PYTHON` |
| `test_pyproject_declares_a_classifier_for_every_supported_interpreter` | a supported interpreter has no classifier |
| `test_the_python_floor_is_the_oldest_supported_interpreter` | `requires-python` and the list disagree |
| `test_the_wheel_claims_every_interpreter_pyproject_declares` | the built `METADATA` claims a different set (B-16) |
| `test_a_wheel_that_lags_behind_on_an_interpreter_is_rejected` | negative control: a stale `METADATA` is accepted |

Negative controls were exercised directly, not assumed:

```console
matrix == SUPPORTED_PYTHONS: True
classifiers == SUPPORTED: True
PASS stale matrix rejected
PASS stale pin rejected: tests.yml pins ['3.13'], but noxfile.PRIMARY_PYTHON is '3.14'
PASS missing 3.14 classifier rejected
```

## Defect found while proving the matrix: `typing.get_overloads` is 3.11+

The 3.10 leg of the newly declared matrix failed on a test unrelated to 3.14:

```console
$ nox -s tests --python 3.10
FAILED tests/unit/test_resolve_contract.py::test_resolve_overloads_share_the_widened_parameter_annotations
E   AttributeError: module 'typing' has no attribute 'get_overloads'
```

`typing.get_overloads` (and the registry behind it) landed in 3.11, so the §5.6 overload
assertion added for goals #3/#4 could not run on the 3.10 floor. It was fixed rather than
skipped: the variants are now read from `lauren_di/_container.py` with `ast`, which works on
every supported interpreter and asserts the annotation *spelling* the PRD fixes
(`dict[Any, Any] | None`, `Mapping[Any, Any] | None`, `type | None`) instead of a `repr` of
an evaluated hint. 3.10 goes from `1 failed, 293 passed` to `294 passed, 3 skipped`.

## Assumption recorded

The user did not answer which shape `PRIMARY_PYTHON` should take before the deadline, so the
reading that satisfies the goal's wording — "3.14 in the classifier, `SUPPORTED_PYTHONS`,
the matrix, `PRIMARY_PYTHON`/docs mentions" — was taken: `PRIMARY_PYTHON` moves to the newest
supported interpreter and the non-matrix CI pins follow it. The alternative (keep 3.13 as the
primary, add 3.14 only to the matrix) is a one-line revert of `noxfile.py:60` plus the ten
`python-version` pins, and the new contract test states which invariant each choice must keep.
