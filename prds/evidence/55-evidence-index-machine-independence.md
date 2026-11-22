# Goal #55 — the evidence index is an observation too

## The question

Goal #48 made the transcript *headers* machine-independent: `header()` writes
`<reference>/lauren/__init__.py` instead of the absolute path it imported. The same
`origin` value was still stored raw in `prds/evidence/output/ids.json`, the index that
summarises every transcript — and nothing read that file. Two consequences, both measured:

1. **The index named the recording host.** `ids.json` carried
   `"reference": "/tmp/ref-clean/lauren/__init__.py"`, so re-recording on a machine that
   keeps the reference elsewhere produced a path-only diff of the one artifact every
   citation audit reads. `tests/unit/test_evidence_transcripts.py` scanned
   `OUTPUT.rglob("*.out")`, so the only file under `output/` that is *not* a `.out`
   transcript was invisible to the check that exists to catch exactly this.
2. **`--check` never compared it.** `run_all.py` guarded the write with
   `if not args.check` and read the index nowhere, so the committed index could disagree
   with the transcripts it summarises and every gate stayed green. That was not
   hypothetical on this tree: goal #53 added `E-409` / `E-409b` to
   `prds/evidence/prd08/p2.py` and its recorded transcript, and the committed `ids.json`
   still listed neither — `--check` passed anyway, because it only ever diffed `.out`
   files.

## Shape chosen

Normalise, don't delete: `reference` keeps its provenance, it just stops naming a host.
The key is worth keeping — it says *which artifact* the ids describe, beside the
`reference_di_sha256` that says which build — so the fix is one helper, not a removal.

| Change | Where |
|---|---|
| `normalise_origin(origin)` — the single rule, applied to both artifacts | `prds/evidence/run_all.py` |
| `header()` calls it instead of inlining the suffix test | `prds/evidence/run_all.py` |
| `summary["reference"]` stores `normalise_origin(origin)` | `prds/evidence/run_all.py` |
| `--check` renders the summary and compares it to `output/ids.json`, reporting `INDEX THAT CHANGED` and failing without writing | `prds/evidence/run_all.py` |
| module docstring: `--check` also fails when the index disagrees | `prds/evidence/run_all.py` |
| the corpus scan covers **every** file under `output/`, and asserts the index is in scope | `tests/unit/test_evidence_transcripts.py` |
| the committed index states `<reference>/lauren/__init__.py` | `prds/evidence/output/ids.json` |

## Measured

`grep -rn "/root/\|/tmp/" prds/evidence/output/` → no matches (exit 1). Before the
re-record it matched `ids.json` and nothing else, exactly as the goal described.

Re-recording (`PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean /usr/bin/python3.14
prds/evidence/run_all.py`, the pinned `PRIMARY_PYTHON`) left **all 23 transcripts
byte-identical** — `md5sum -c` over a snapshot taken beforehand reported 23 × `OK`, and
`git status --porcelain prds/evidence/output/` showed only `ids.json` (plus `prd08/p2.out`,
which goal #53 had already modified). The index diff is therefore the two things it should
be: the normalised `reference`, and the `E-409` / `E-409b` ids that #53's probe emits and
the committed index had been missing.

Determinism: three consecutive runs (record, record, `--check`) left `ids.json` at the same
`md5 d13bc4688aa276e5d8aea9ce67d8cad8`, and `--check` reported `RESULT: PASS` while writing
nothing.

## Drills — each property broken on purpose

| Drill | Result |
|---|---|
| absolute path reintroduced into `ids.json` (`<reference>` → `/tmp/ref-clean`) | **2 failed**, 5 passed — `test_no_recorded_output_names_an_absolute_path` and `test_the_recorded_index_names_the_artifact_not_the_machine`; restored `md5sum -c` → `OK` |
| committed index perturbed (one trailing newline) | `--check` printed `INDEX THAT CHANGED : output/ids.json (disagrees with this run)` and exited **1**; restored `md5sum -c` → `OK` |
| recorder run twice | identical `md5`; `--check` afterwards PASS and no file written |
| `--check` after a fresh record | exit 0 (a correct index is not reported as drift) |

The `--check` comparison is pinned by `test_check_fails_when_the_recorded_index_disagrees`,
which redirects `OUTPUT` at a temp directory, records, checks (pass), perturbs, checks
(fail) — so the comparison cannot be dropped without a red test.

## Gates

| Gate | Result |
|---|---|
| `python -m pytest -q` | **662 passed** |
| `python -m pytest tests/unit -q` | 367 passed |
| `python -m pytest tests/unit/test_evidence_transcripts.py -q` | 7 passed |
| `nox -s prds_evidence` | green (with `PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean`) |
| `python prds/check_consistency.py` | all checks passed |
| `nox -s lint format` | clean / 71 files formatted |

`AGENTS.md`'s quick-start figure moved `604 / 578` → **`606 / 580`** (+2 tests, measured
with `--collect-only` on 3.13 and on `.nox/tests-3-10`; `606 + 56 parity = 662`).

## Why the guard is in two places

The scan answers "does any recorded file name a host?"; the `--check` comparison answers
"does the index still describe the corpus?" They fail on different defects — a normalised
index that has drifted, and an absolute path in a transcript — and the first is a
`pytest`-only property while the second is a property of the gate itself. Both are
measured here rather than asserted.
