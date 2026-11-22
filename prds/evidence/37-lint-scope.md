# Goal #37 — scope the lint target so cited evidence cannot redden it

## The defect

Goal #36 ended with "add `prds/` to the `lint`/`format` ruff targets", and its
verification claim read "`ruff check prds` and `ruff format --check prds` are
clean". Against the directory *as written* that claim is unsatisfiable:

```
$ ruff check . --output-format=concise | awk -F: '{print $1}' | cut -d/ -f1-2 | sort | uniq -c
    316 prds/evidence
      1 prds/check_consistency.py
```

`prds/evidence/**` is not scratch. It holds the probes behind the `E-nnn` ids
that PRDs 02-08 quote, plus their recorded transcripts; `prds/evidence/run_all.py`
fails when a cited id is emitted by no probe, so reformatting a probe risks
invalidating the citation chain, and `tests/unit/test_gitignore_contract.py::EVIDENCE_BASE`
asserts the same files stay *committed*. A lint target that can only be green by
rewriting them is the wrong target.

Goal #36 landed the shape goal #37 asked for, one directory wide:

```toml
[tool.ruff]
extend-exclude = [..., "prds/evidence"]   # with the reason, naming EVIDENCE_BASE
```

`extend-exclude`, not `exclude`: the latter *replaces* ruff's default list, so
`dist/`, `site/` and every virtualenv would start being linted. What #37 adds is
the part that keeps that sentence true — the exclusion was only *declared*, and a
declaration cannot tell a one-directory exclusion from a dead one.

## What changed

| File | Change |
| --- | --- |
| `tests/unit/test_noxfile_contract.py` | `ruff_config()` / `extend_exclude()` / `_ruff()` helpers; the existing declaration test narrowed (the entry must be `prds/evidence` and nothing else under `prds`, and must be `extend-exclude`); three new tests: ruff *resolves* the checker and no probe, the exclusion is load-bearing, and the excluded directory is exactly the cited evidence |
| `prds/evidence/36-prds-gate.md` | records the amendment: the target is the directory *with* the exclusion, which is what makes "`ruff check prds` is clean" true |
| `AGENTS.md` | the machine-checked test count moves 516 → 519 (490 → 493 on 3.10) for the three new tests |

The three new tests, and why each is not decoration:

1. **`test_ruff_resolves_the_prd_checker_and_no_probe`** — runs
   `ruff check prds --show-files`, which prints the corpus ruff resolves, and
   asserts the checker is in it and no path under `prds/evidence/` is. It asks
   both with and without `--force-exclude`, because the two readings differ:
   `nox -s lint` passes `prds` *explicitly* and ruff does not apply excludes to
   an explicitly passed path unless forced. Widening the entry to `prds` is
   invisible to the plain form (measured below) and obvious to the forced one.
2. **`test_the_evidence_exclusion_is_load_bearing`** — an explicitly passed path
   bypasses `extend-exclude`, so `ruff check prds/evidence` is the one way to
   ask what the probes look like without the entry. It fails if that comes back
   clean, i.e. if the entry stops protecting anything.
3. **`test_the_excluded_directory_is_exactly_the_cited_evidence`** — reads the
   recorded audit `prds/evidence/output/ids.json` (`cited`, `probes`,
   `cited_not_emitted`) and asserts every cited id is emitted by a probe that is
   on disk *under* `prds/evidence/`. A probe that moved outside the excluded
   directory would start being linted; a citation chain that drifted is what
   reformatting would hide.

None of them assert a violation count, so a new ruff release that adds a rule
cannot redden them, and `RUFF_VERSION` in `noxfile.py` stays the single place a
version is stated.

## Drills

Each drill edits `pyproject.toml`, runs
`pytest tests/unit/test_noxfile_contract.py -k "ruff or evidence or cited"`, and
restores the file (`git diff --stat pyproject.toml` is empty afterwards).

```
A: extend-exclude gains "prds"          -> 2 failed (declaration + --show-files)
B: "prds/evidence" removed              -> 2 failed (declaration + --show-files)
C: extend-exclude renamed to exclude    -> 1 failed (declaration)
```

Drill A is the one that motivated the `--force-exclude` reading. Measured, with
the entry widened to `"prds"`:

```
$ ruff check prds --show-files                     # what nox -s lint runs
.../prds/check_consistency.py                      # explicit path wins: widening invisible
$ ruff check prds --show-files --force-exclude
                                                   # (nothing: the checker is excluded)
```

With the shipped entry, both readings print exactly `prds/check_consistency.py`.

## Verification

```
$ ruff check prds
All checks passed!
$ ruff format --check prds
1 file already formatted
$ ruff check .                      # whole repo, evidence excluded
All checks passed!
$ git diff --stat prds/evidence     # the probes are untouched
(empty)

$ ruff check prds/evidence --output-format=concise ; echo exit=$?
prds/evidence/prd08/p5.py:136:22: E702 Multiple statements on one line (semicolon)
...
Found 316 errors.
exit=1                              # the exclusion is load-bearing, not dead config

$ python -m pytest tests/unit/test_noxfile_contract.py tests/unit/test_gitignore_contract.py -q
45 passed
$ nox -s prds
  ok   index / template / links / cross-refs / decisions / symbols / scope / owners
all checks passed                    (exit 0, eight ok lines)

$ grep -n "nox -s prds" .github/workflows/lint.yml
      - run: nox -s prds             # the CI job still gates the checker, not ruff
```

`tests/unit/test_gitignore_contract.py::EVIDENCE_BASE` still passes, so the
exclusion cannot widen into a claim that the probes are scratch: the same files
are still asserted *committed*, and `ids.json` — the recorded transcript the
third test reads — is one of them.
