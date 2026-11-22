# Goal #31 — the documented test count is now a checked claim

## What was stale

`AGENTS.md:27` read:

```text
pytest -q                      # full suite (114 tests, <2s)
```

Both figures were wrong by the time anyone looked: the suite had grown to 450
tests, and a full run takes ~17 s on this machine, not under two. Nothing read
the number, so nothing noticed — the same failure class as a documented gate
that no job runs.

`CLAUDE.md` and `README.md` were checked for the same claim: **neither states a
test count.** `CLAUDE.md` lists sessions without counts; `README.md` names
`nox -s tests` with no figure. Nothing to refresh there.

## The replacement, and why it is not just `pytest -q`'s total

```text
pytest -q                      # full suite: 450 tests (+52 parity with ../lauren-framework)
```

`tests/parity/test_matrix.py:32-40` skips itself at **import** time when the
`../lauren-framework` checkout is absent, so the collected total depends on the
machine:

| Environment | `pytest -q` collects |
| --- | --- |
| no parity at all (`--ignore=tests/parity`) | **450** |
| no reference checkout (matrix module skipped, harness collected then skipped) | 476 |
| with the reference checkout | **502** |

A documented number that changes with the environment is the same defect as a
gate that does, which is why `[tool.coverage.report]` already quotes its figure
"over the suite that runs *everywhere*" and excludes parity. The quick start
follows that precedent: it states the environment-independent 450 and says what
parity adds (52 = 26 in `test_matrix.py` + 26 in `test_harness.py`).

## The gate

`tests/unit/test_documented_test_count.py` (4 tests) turns the figure into an
assertion:

1. the documented base equals `pytest --collect-only -q --ignore=tests/parity`
   in a **separate process** — the same number a contributor gets by typing the
   documented command, not this session's own view of itself;
2. when the checkout is present, `base + parity` equals the full collected
   total (skips, naming the path, when it is absent);
3. the line names `../lauren-framework`, so the 450 figure cannot be left
   looking wrong on a machine that has the checkout;
4. no other document in the corpus states an unchecked count — a second figure
   would be a second stale claim.

`PYTEST_*` and `COV_CORE_*` are stripped from the probe's environment and
`-p no:cacheprovider` is passed, so the collection run is inert even when the
outer session is `nox -s coverage`.

## Drills

```text
$ sed -i 's/full suite: 450 tests/full suite: 449 tests/' AGENTS.md
$ pytest tests/unit/test_documented_test_count.py -q
E  AssertionError: AGENTS.md's quick start says 449 tests, but
   `pytest -q --ignore=tests/parity` collects 450. Update the line — the count
   is a claim, not decoration.
2 failed, 2 passed

$ echo 'Run `pytest -q` (346 tests) for the suite.' >> CLAUDE.md
$ pytest tests/unit/test_documented_test_count.py -q
E  AssertionError: a test count is documented outside AGENTS.md's checked
   quick-start line, so nothing keeps it true: ['CLAUDE.md:199: … (346 tests) …']
1 failed, 3 passed
```

Both mutations were reverted; `git status --porcelain` shows only the intended
`AGENTS.md` edit and the new test module.

## Gates

`nox -s docs_contract llms_check lint format` — 4 sessions successful.
`pytest tests/unit -q` — 276 passed. `python prds/check_consistency.py` — exit 0.
