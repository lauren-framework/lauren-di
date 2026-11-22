# 50 — every python block in the PRDs is compiled, and every `run` block executes

## The defect

The corpus held **88 fenced `python` blocks across the eight PRD pages** and no gate
read one of them:

* `scripts/check_docs_contract.py` compiles blocks, but its source list has no
  `prds/` page — the corpus it gates is `README.md` and `docs/*.md`;
* `prds/check_consistency.py` parsed prose, links and tables, never a fence;
* `tests/docs/test_prd_examples.py` named two sections — PRD 01 §5.4 (22 blocks)
  and PRD 03 §5.3 (1 block).

So 65 blocks were compiled by nothing and executed by nothing. Three of them were
complete programs ending in `asyncio.run(main())`.

The rot was not hypothetical. PRD 03 §5.11's lifecycle example — the block that
walks a request scope from `startup()` to `shutdown()` — raised on the first run:

```
DIScopeViolationError: Tx is request-scoped and there is no active scope
```

Its tail resolved a request-scoped `Tx` after the `async with` block had closed.
The page had been read many times; nothing executed it.

## The fix

`prds/check_consistency.py` gained a sixth check, `blocks`, and the corpus gained a
marker on every fence:

* **every block compiles**, including the ones marked `fragment`/`skip` — the marker
  excuses a block from *execution*, not from being Python;
* **every block carries a marker**. `run` promises the block executes, and
  `tests/docs/test_prd_examples.py` now executes every `run` block in every PRD page;
  `fragment` (alias `skip`) is the explicit "deliberately partial, compile only". An
  unmarked block is one no gate has decided about — which is exactly how 65 blocks
  went unexecuted;
* a block marked both `run` and `fragment`/`skip` fails, because it either executes
  or it does not.

The grammar is not re-derived: `check_blocks()` imports
`scripts/check_docs_contract.py` and calls its `extract_blocks()`, the same reader
`tests/docs/prd_examples.py` uses. One fence grammar, two gates.

The census is printed as an `ok` line, so the corpus's shape is visible and not only
its failures:

```
  ok   blocks: 88 python block(s) in 8 PRD page(s) — 88 compiled, 28 marked run, 60 fragment/skip, 0 unmarked
```

PRD 03 §5.11 was repaired rather than excused: its tail now opens a second
`async with c.scope()` and resolves there, and the prose above it moved from
"Twenty-one non-blank lines" to "Twenty-seven" — the measured count.

## Before / after

Each probe was run on the fixed tree and the page restored afterwards
(`git status` clean before the next one):

```
$ python - <<'PY'   # strip the marker from PRD 08's first python fence
...
$ python prds/check_consistency.py
  ok   blocks: 88 python block(s) in 8 PRD page(s) — 88 compiled, 28 marked run, 59 fragment/skip, 1 unmarked
  FAIL [blocks] 08-framework-compatibility-and-migration.md:173: python block carries no marker —
                `run` if a reader is meant to run it, `fragment`/`skip` if it is deliberately partial;
                an unmarked block is one no gate has decided about
1 failure(s)
exit=1
```

```
$ python - <<'PY'   # insert `def broken(:` at the top of a `fragment` block
...
$ python prds/check_consistency.py
  ok   blocks: 88 python block(s) in 8 PRD page(s) — 87 compiled, 28 marked run, 60 fragment/skip, 0 unmarked
  FAIL [blocks] 08-framework-compatibility-and-migration.md:173: python block does not compile:
                invalid syntax (line 1)
1 failure(s)
exit=1
```

```
$ python - <<'PY'   # put `raise RuntimeError("probe")` in a `run` block
...
$ python -m pytest tests/docs/test_prd_examples.py -q
FAILED tests/docs/test_prd_examples.py::test_every_run_marked_block_in_the_page_executes[03-scopes-lifecycle-and-disposal.md]
1 failed, 44 passed
```

The third probe is the half `blocks` cannot see: `run` is a *promise*, and the
promise is kept or broken in the test module.

## The tests that pin it

`tests/docs/test_prd_examples.py` grew three tests (ten cases, because the per-page
execution is parametrised over the eight pages):

| Test | What it decides |
| --- | --- |
| `test_the_corpus_census_is_the_one_this_module_executes` | the corpus is 88 blocks over 8 pages, 28 of them `run` — the same split `blocks` reports |
| `test_no_prd_block_is_unmarked` | a fence with no marker fails here as well as in `blocks` |
| `test_every_run_marked_block_in_the_page_executes[...]` | every `run` block in PRD 01–08 executes, per page, in file order |

`tests/unit/test_resolve_contract.py` read PRD 01 §5.6 with a regex of its own
(`` ```python\n ``); the §5.6 block is now `python fragment`, so the helper reads
the block through `scripts/check_docs_contract.py` instead of growing a second
grammar.

## Verification

| Check | Result |
| --- | --- |
| `python prds/check_consistency.py` | exit 0, all ten `ok` lines, `blocks: 88 … 88 compiled, 28 run, 60 fragment/skip, 0 unmarked` |
| marker stripped from PRD 08 | exit 1, names `08-…md:173` and the missing marker |
| syntax error inside a `fragment` block | exit 1, names the file and line |
| `raise RuntimeError` inside a `run` block | `tests/docs` fails the page's execution test |
| `python -m pytest tests/docs -q` | 58 passed |
| `python -m pytest -q` | 646 collected (594 without the parity checkout; 620 / 568 on 3.10) |
| `AGENTS.md` quick-start count | 584 / 558 / +52 → **594 / 568 / +52**, re-measured with `--collect-only` |
| `nox -s ci` | green in 5 minutes — `ci: all sessions passed` (with `PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean`) |
| GitHub Actions on `43da4c3` | `lint`, `tests`, `docs` and `codeql` all `completed/success` |
