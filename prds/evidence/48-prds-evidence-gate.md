# Goal #48 — gate `prds/evidence/run_all.py --check`

## The defect

`grep -rn "run_all"` found the runner only in prose: `GOALS.md`, the
`pyproject.toml` `extend-exclude` comment, PRD 06 §6.6, `prds/evidence/README.md`
and two test docstrings. No nox session, no workflow job, no test ran it — so the
23 probes, the 260 emitted `E-nnn` ids and the 188 ids cited across `prds/*.md`
were re-audited only when a human typed the command. Same failure class goals #36
(`prds/check_consistency.py`) and #37 (the lint scope over `prds/evidence/`)
closed one and two levels up: a check nobody runs is not a check. It was also what
kept #37's "never reformat cited evidence" rule a matter of trust — reformatting a
probe changes the transcript it produces, and nothing compared the two.

## What was added

| Piece | Change |
| --- | --- |
| `noxfile.py` | `prds_evidence` session (`--check`, unconditional, posargs appended), `PRDS_EVIDENCE_DIR`/`PRDS_EVIDENCE_RUN_ALL` constants, and the session wired into the `ci` aggregator |
| `.github/workflows/lint.yml` | `prds-evidence` job: clones the reference at the same pinned `REFERENCE_SHA` as `tests.yml:parity`, then `nox -s prds_evidence` |
| `tests/unit/test_noxfile_contract.py` | six tests: the gate mode, posargs, the reference dependency, the interpreter refusal, session↔CI wiring in both directions, the job's pinned checkout, and parity of the two `REFERENCE_SHA` pins |
| `pyproject.toml` | the `extend-exclude` comment no longer cites `--check` as if nothing ran it |
| `prds/evidence/README.md` | the gate, the interpreter pin and the link normalisation |
| `prds/evidence/run_all.py` | `header()` writes `<reference>` instead of an absolute path, `reference_dirty_paths()` refuses a dirty checkout, `PRDS_EVIDENCE_REFERENCE` overrides the path, and the drift line names the probe *and* the transcript |

## Measurements

`python3 prds/evidence/run_all.py --check` on `f2d9a34`:

```
cited ids in prds/*.md : 188
emitted by probes      : 260
reserved in PRD 06 §6.6: 0
emitted but not cited  : 72 (allowed)
RESULT: PASS
```

14 s wall clock, and `git status --porcelain` empty afterwards — the goal text's
"~20 s" is the conservative figure; the session docstring states the measured one.

`nox -s prds_evidence` (primary interpreter, 3.14):

```
nox > Session prds_evidence was successful in 15 seconds.
RESULT: PASS
```

The probe count is **23**, not the 24 the goal text states (`run_all.py --list`
prints 23 rows; `prds/evidence/README.md` already said 23). The goal's other
figures — 260 emitted, 188 cited — match exactly.

## Two findings the gate produced immediately

Neither was visible while the runner was hand-typed, because nobody typed it on
the interpreter and the path that CI uses.

### 1. The transcripts recorded CPython, not `lauren`, in two places

The recorded corpus was captured on Python 3.13.13; the primary interpreter (and
the CI job) is 3.14. Running `--check` on 3.14 reported drift in two of the 24
transcripts:

```
-E-53 Depends[fn] raw annotation: typing.Annotated[fn_other, <class 'lauren.extractors.Depends'>]
+E-53 Depends[fn] raw annotation: typing.Annotated[__main__.fn_other, <class 'lauren.extractors.Depends'>]
-E-306c optional flag is not consulted by the container     -> ValueError | detail: None | msg: a coroutine was expected, got False
+E-306c optional flag is not consulted by the container     -> TypeError | detail: None | msg: An asyncio.Future, a coroutine or an awaitable is required
```

Both are interpreter artifacts, not reference behaviour: 3.14 prefixes a
function's repr with its module (`__main__.fn_other`) and rewords asyncio's
`ensure_future` rejection (and raises `TypeError` rather than `ValueError`). No
probe was touched — the probes are cited evidence and stay byte-identical — the
transcripts were **re-recorded** with the runner's own documented record mode
(`.nox/prds_evidence/bin/python prds/evidence/run_all.py`), which is the
supported way to move the corpus. Diff across all 24 transcripts: 2 lines, the
two above.

The gate is therefore pinned to `PRIMARY_PYTHON`: the session refuses any other
interpreter with a message naming this reason, rather than reporting drift that
is not a change in `lauren`. A future move to another interpreter is a deliberate
re-record, not a silent red build.

### 2. The recorded header named the machine, not the artifact

The first two CI runs of the new job were red on **all 24 transcripts**, each
differing only in the header line that records `lauren.__file__`:

```
-# lauren.__file__: /root/python_projects/lauren-all/lauren-framework/lauren/__init__.py
+# lauren.__file__: /home/runner/work/lauren-di/lauren-framework/lauren/__init__.py
```

An absolute path in a recorded file makes the comparison machine-specific by
construction. The first attempt normalised it at comparison time, which was a
shim: the recorded side names a root that does not exist on the runner, so only
the freshly produced side was rewritten. The fix is at the source —
`header()` writes `<reference>/lauren/__init__.py` plus the
`_di/__init__.py` sha256 — so the comparison is plain byte equality again, with no
normalisation to get wrong, and the corpus carries no absolute path at all
(`tests/unit/test_evidence_transcripts.py` asserts that over every recorded
`.out`). All 24 transcripts were re-recorded; the header is the only thing that
changed in them.

### 3. The corpus had been recorded against a *dirty* reference checkout

With the header fixed, one line still differed:

```
-E-402f len(lauren.__all__)          -> 295
+E-402f len(lauren.__all__)          -> 294
```

`lauren.__all__` has 295 names in this machine's sibling checkout and 294 at the
pinned commit `0009d439`, because the checkout has uncommitted edits — and the
`_di/__init__.py` sha256 in every header is **identical** in both, so the pin's
existing identity check cannot see them. The corpus described a working tree
nobody can reproduce from the pin. It was re-recorded against a clean clone of
`0009d439` (`git clone … /tmp/ref-clean && git -C /tmp/ref-clean checkout <sha>`,
which writes nothing inside the reference repository), and the runner now refuses
a dirty checkout outright, naming `PRDS_EVIDENCE_REFERENCE` as the way to point it
at a clean copy. CI never sets it: it clones the pin into
`<repo>/../lauren-framework`, which is clean by construction.

## Verification

| Check | Result |
| --- | --- |
| `nox -s prds_evidence` (CI shape: clean reference at the pin) | green in 15 s, `RESULT: PASS` |
| `nox -s prds_evidence` on this machine's *dirty* sibling checkout | exit 1, `REFUSING: the reference checkout has 6 uncommitted path(s)` plus the `PRDS_EVIDENCE_REFERENCE` recipe — the finding above, not a regression |
| probe drift (one printed label reformatted in `prd04/a.py`) | exit 1, `transcripts that changed: 1` / `- prd04/a.py -> output/prd04/a.out` / `RESULT: FAIL` |
| transcript edit (newline appended to `output/prd04/a.out`) | exit 1, same line, `RESULT: FAIL` |
| `("prds_evidence", [])` deleted from `ci` | `test_no_defined_session_escapes_the_aggregator` and `test_the_prds_evidence_gate_is_wired_into_both_the_aggregator_and_ci` fail |
| `python -m pytest tests/unit/test_noxfile_contract.py tests/unit/test_evidence_transcripts.py -q` | green |
| `ruff check` / `ruff format --check` on `noxfile.py`, the contract test and the transcript test | clean |
| `python prds/check_consistency.py` | exit 0 |
| `nox -s ci` | green |
