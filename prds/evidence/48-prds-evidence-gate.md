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
| `prds/evidence/run_all.py` | `normalise_reference()` for the comparison; the drift line names the probe *and* the transcript |

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

### 2. The recorded header carries an absolute path

Every transcript records `lauren.__file__`, e.g.
`/root/python_projects/lauren-all/lauren-framework/lauren/__init__.py`. Comparing
those bytes literally makes `--check` pass on exactly one machine — the one that
recorded them — so the new CI job would have been red on its first run, in a way
that says nothing about the evidence. `normalise_reference()` replaces the
reference root with `<reference>` **on both sides of the comparison only**:

* nothing on disk is rewritten, so the transcripts stay byte-identical to what
  the PRDs cite;
* the `_di/__init__.py` sha256 line beside it is still compared verbatim, so a
  different reference build is still drift — which is what keeps the pin honest.

## Verification

| Check | Result |
| --- | --- |
| `nox -s prds_evidence` | green in 15 s, `RESULT: PASS` |
| probe drift (one printed label reformatted in `prd04/a.py`) | exit 1, `transcripts that changed: 1` / `- prd04/a.py -> output/prd04/a.out` / `RESULT: FAIL` |
| transcript edit (newline appended to `output/prd04/a.out`) | exit 1, same line, `RESULT: FAIL` |
| `("prds_evidence", [])` deleted from `ci` | `test_no_defined_session_escapes_the_aggregator` and `test_the_prds_evidence_gate_is_wired_into_both_the_aggregator_and_ci` fail |
| `python -m pytest tests/unit/test_noxfile_contract.py -q` | 48 passed |
| `ruff check` / `ruff format --check` on `noxfile.py` and the contract test | clean |
| `python prds/check_consistency.py` | exit 0 |
| `nox -s ci` | green |
