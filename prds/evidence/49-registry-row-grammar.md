# 49 — the §2/§3 registry reader is loud about rows it cannot read

## The defect

`GOALS.md`'s two registry tables are the only in-repo definition of a `#NN` runner
position, and `prds/check_consistency.py`'s `owners` check resolves every reference
against them. The reader that built that registry, though, asked each row *"does the
first cell look like a number?"* and answered **"no"** by returning silently:

```python
def spread(cell: str, description: str) -> None:
    m = re.fullmatch(r"#?(\d+)(?:[–-]#?(\d+))?", cell)
    if not m:
        return                      # <- the row disappears, with no failure
```

A second skip sat one line above it: the carried-table loop required
`len(cells) >= 3`, so a two-cell row was dropped before `spread` ever saw it.

Both were verified on `3cb1c16` by inserting a row after `| 38 | … |` and running
`python prds/check_consistency.py` — which exited **0** both times:

| Inserted row | Old behaviour |
| --- | --- |
| `\| **44** \| **verified** \| a goal the parser cannot see \|` | registers nothing (`spread` does not match a bolded cell) |
| `\| 44 \| a goal with no status cell \|` | dropped by the `len(cells) >= 3` guard |

Nothing else noticed either: the row is not a `### #NN` heading, so the heading pass
never sees it, and no `#NN` reference exists to fail. The goal was simply gone from
the file's own definition of itself while the gate stayed green — the same
"well-formed but unchecked" shape the collision rule closed one step later, one step
earlier in the pipeline.

## The fix

`prds/check_consistency.py` now reads the tables *positionally* and reports what it
cannot read.

* `_registry_rows()` walks `GOALS.md` line by line, tracking the `## 2.` / `## 3.`
  section, and yields `(line number, section label, cells)` for every table row that
  is neither the separator (`|---|`) nor the header (the row immediately above a
  separator). No row can be skipped by looking wrong.
* `_spread()` returns `[]` for anything that is not a bare number or an ascending
  range — so a reversed `#6–#2` is unreadable rather than silently zero goals wide.
* `_parse_registry()` returns `(entries, problems)`. `problems` is non-empty when a
  data row registers no goal: a first cell that is not a number/range, or a row too
  short to reach its table's descriptive cell.
* `_check_goal_numbering()` reports each problem as `fail("owners", …)`, naming the
  file, the line and the row.
* The descriptive cell is named **per table** — cell 2 for §2's
  `| Goals | Delivered | Evidence |`, cell 3 for §3's `| # | Status | Item |` — so
  the rule does not simply demand three cells (which would reject a valid §2 row).
* `check_owners()` now reports its own census, so a row that left the file is visible
  as a count and not only as a failure:

```
  ok   registry: 35 §2/§3 data row(s) read, 44 runner position(s) registered, 0 unreadable
```

## Before / after

Both probes re-run on the fixed tree (`GOALS.md` restored afterwards each time):

```
$ python - <<'PY'   # insert `| **44** | **verified** | a goal the parser cannot see |` after row 38
...
$ python prds/check_consistency.py
  ok   registry: 35 §2/§3 data row(s) read, 43 runner position(s) registered, 1 unreadable
  FAIL [owners] GOALS.md:88: §3 registry row's first cell must be a bare number or a range
                (`23`, `#2–#6`), not '**44**': | **44** | **verified** | a goal the parser cannot see |
1 failure(s)
exit=1
```

```
$ python - <<'PY'   # insert `| 44 | a goal with no status cell |` after row 38
...
$ python prds/check_consistency.py
  ok   registry: 35 §2/§3 data row(s) read, 43 runner position(s) registered, 1 unreadable
  FAIL [owners] GOALS.md:88: §3 registry row has 2 cell(s), but its description is cell 3:
                | 44 | a goal with no status cell |
1 failure(s)
exit=1
```

## The tests that pin it

`tests/unit/test_goal_numbering_contract.py` grew from 9 to 12 tests. Each rejected
shape is pinned by the mutation it must reject, driven against a synthetic plan in
`tmp_path` (the real `GOALS.md` is never touched):

| Test | Fails on the old reader? |
| --- | --- |
| `test_a_bolded_position_cell_fails` | **yes** — `a bolded number cell was silently skipped` |
| `test_a_row_too_short_to_reach_its_description_fails` | **yes** — `a row with no descriptive cell was silently dropped` |
| `test_the_required_width_is_the_table_s_own_not_three` | no (it guards the *other* direction: a two-cell §2 row must still register) |

Measured by stashing the parser change and re-running the module:

```
$ git stash push prds/check_consistency.py && python -m pytest tests/unit/test_goal_numbering_contract.py -q
FAILED tests/unit/test_goal_numbering_contract.py::test_a_bolded_position_cell_fails
FAILED tests/unit/test_goal_numbering_contract.py::test_a_row_too_short_to_reach_its_description_fails
2 failed, 10 passed in 0.32s
$ git stash pop && python -m pytest tests/unit/test_goal_numbering_contract.py -q
12 passed in 0.35s
```

The third test cannot fail on the old reader — it exists to stop the fix from
over-correcting into a "every row needs three cells" rule, which would reject §2's
valid two-column rows.

## Verification

| Check | Result |
| --- | --- |
| `python prds/check_consistency.py` | exit 0, all nine `ok` lines, `registry: 35 … 0 unreadable` |
| bolded first cell inserted into §3 | exit 1, names `GOALS.md:88` and the row |
| two-cell §3 row inserted | exit 1, names `GOALS.md:88` and the row |
| `python -m pytest tests/unit/test_goal_numbering_contract.py -q` | 12 passed |
| `python -m pytest -q` | 636 collected (584 without the parity checkout) |
| `AGENTS.md` quick-start count | 584 / 558 / +52 parity, re-measured with `--collect-only` |
| `nox -s lint format prds` | clean |
| `nox -s ci` | green in 4 minutes — `ci: all sessions passed`, with the `prds` session printing the new `registry:` line |
| GitHub Actions on `bcf8b08` | `lint`, `tests`, `docs` and `codeql` all `completed/success` |
