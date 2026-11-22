# Goal #31 — the documented test count is now a checked claim

## What was stale

`AGENTS.md:27` read:

```text
pytest -q                      # full suite (114 tests, <2s)
```

Both figures were wrong by the time anyone looked: the suite collects 451 tests
on 3.11+ and a full run takes ~17 s on this machine, not under two. Nothing read
the number, so nothing noticed — the same failure class as a documented gate
that no job runs.

`CLAUDE.md` and `README.md` were checked for the same claim: **neither states a
test count.** `CLAUDE.md` lists sessions without counts; `README.md` names
`nox -s tests` with no figure. Nothing to refresh there.

## Why one number is impossible

The first attempt documented a single figure, 450, and CI failed on the 3.10 leg:

```text
E  AssertionError: AGENTS.md's quick start says 450 tests, but
   `pytest -q --ignore=tests/parity` collects 425.
```

Two independent things move the collected total:

| Cause | Effect |
| --- | --- |
| `tests/parity/test_matrix.py:32-40` skips itself at **import** time when `../lauren-framework` is absent | 52 cases appear only with the checkout (26 matrix + 26 harness) |
| `tests/unit/test_readme_contract.py:42` does `pytest.importorskip("tomllib")` at module level, and `tomllib` is stdlib only from 3.11 | that module's 25 tests are never collected on 3.10 |

450 − 425 = 25, exactly the size of `test_readme_contract.py` — so the 3.10 gap
is fully explained, not a mystery. A single number would therefore be false
somewhere, and a documented number that changes with the environment is the same
defect as a gate that does. `[tool.coverage.report]` already reasons this way,
quoting its figure "over the suite that runs *everywhere*" and excluding parity.

## The replacement

```text
pytest -q                      # full suite: 451 tests (426 on 3.10); +52 parity with ../lauren-framework
```

All three figures are stated, so none of them is a lie on any of the five
supported interpreters.

## The gate

`tests/unit/test_documented_test_count.py` (5 tests):

1. the figure for the **running** interpreter equals `pytest --collect-only -q
   --ignore=tests/parity` measured in a **separate process** — the number a
   contributor gets by typing the documented command, not this session's view of
   itself;
2. the 3.10 figure is checked from every interpreter: `expected_count()` is a
   pure function, so the branch is exercised on 3.14 too, and on 3.11+ the
   modern−legacy difference is asserted to be exactly the size of
   `tests/unit/test_readme_contract.py`;
3. with the checkout present, base + parity equals the full collected total;
4. the line names `../lauren-framework`, so the base figure cannot be left
   looking wrong on a machine that has the checkout;
5. no other document in the corpus states an unchecked count — a second figure
   would be a second stale claim.

`PYTEST_*` and `COV_CORE_*` are stripped from the probe's environment and
`-p no:cacheprovider` is passed, so the collection run is inert even when the
outer session is `nox -s coverage`. No assertion is skipped on any interpreter
except the parity-addition test, which needs the checkout *and* `tomllib`.

## Drills

```text
$ sed -i 's/full suite: 451 tests/full suite: 449 tests/' AGENTS.md
$ pytest tests/unit/test_documented_test_count.py -q
E  AssertionError: AGENTS.md's quick start implies 449 tests on Python 3.14,
   but `pytest -q --ignore=tests/parity` collects 451. Update the line — the
   count is a claim, not decoration.
2 failed, 3 passed

$ echo 'Run `pytest -q` (346 tests) for the suite.' >> CLAUDE.md
$ pytest tests/unit/test_documented_test_count.py -q
E  AssertionError: a test count is documented outside AGENTS.md's checked
   quick-start line, so nothing keeps it true: ['CLAUDE.md:199: … (346 tests) …']
1 failed, 4 passed
```

Both mutations were reverted.

## The lesson recorded

A gate that asserts an environment-dependent number is not a gate: the first
version of this test was green locally and red on 3.10 for a reason that had
nothing to do with the doc being wrong. The fix is not to skip the other
interpreters but to state the figure each of them actually collects.
